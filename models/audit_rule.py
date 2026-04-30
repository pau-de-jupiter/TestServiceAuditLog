import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Маркер защиты от двойного патча одной модели.
# Поля отслеживания не кешируются в патче — audited_write читает их из БД при каждом вызове,
# поэтому добавление/удаление полей в audit.rule применяется сразу без повторного патча.
_AUDIT_PATCHED_ATTR = '_audit_log_patched'


class AuditRule(models.Model):
    _name = 'audit.rule'
    _description = 'Audit Rule'

    name = fields.Char(
        string='Name',
        required=True,
    )
    model_id = fields.Many2one(
        comodel_name='ir.model',
        string='Model',
        required=True,
        ondelete='cascade',
    )
    model_name = fields.Char(
        related='model_id.model',
        string='Model Name',
        store=True,
        readonly=True,
    )
    field_ids = fields.Many2many(
        comodel_name='ir.model.fields',
        relation='audit_rule_field_rel',
        column1='rule_id',
        column2='field_id',
        string='Tracked Fields',
        domain="[('model_id', '=', model_id), ('ttype', 'not in', ['one2many', 'many2many'])]",
    )
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('unique_model', 'unique(model_id)', 'An audit rule for this model already exists.'),
    ]

    # ------------------------------------------------------------------
    # ORM overrides — re-apply patches when rules change
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._patch_models()
        records._invalidate_tracked_fields_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._patch_models()
        self._invalidate_tracked_fields_cache()
        return result

    def unlink(self):
        self._invalidate_tracked_fields_cache()
        for rule in self:
            _logger.warning(
                'audit.rule: правило для модели %s удалено. '
                'Патч write() останется активным до перезапуска сервера. '
                'Для немедленного эффекта — деактивируйте правило вместо удаления.',
                rule.model_name,
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Hook — called by Odoo registry after all models are loaded
    # ------------------------------------------------------------------

    @api.model
    def _register_hook(self):
        """Применяет патчи write() для всех активных правил при запуске реестра."""
        super()._register_hook()
        self.search([('active', '=', True)])._patch_models()

    # ------------------------------------------------------------------
    # Patching logic
    # ------------------------------------------------------------------

    def _invalidate_tracked_fields_cache(self):
        """Сбрасываем кеш отслеживаемых полей для затронутых моделей."""
        for rule in self:
            # model_name — related stored поле. В редком случае (удаление правила до
            # вычисления stored значения) оно может быть False, что даст ключ
            # '_audit_tracked_fields_False' и не инвалидирует нужную запись.
            if not rule.model_name:
                _logger.warning(
                    'audit.rule: не удалось инвалидировать кеш для правила id=%s — '
                    'model_name пустой. Кеш будет сброшен при следующем write() автоматически.',
                    rule.id,
                )
                continue
            cache_key = '_audit_tracked_fields_%s' % rule.model_name
            self.env.registry.__dict__.pop(cache_key, None)

    def _patch_models(self):
        """Dynamically wrap write() for each model that has an active rule."""
        for rule in self.filtered('active'):
            model_name = rule.model_id.model
            if model_name not in self.env:
                _logger.warning('audit.rule: model %s not found in registry', model_name)
                continue

            # type() — получаем класс модели, а не экземпляр recordset.
            # В Odoo 16 атрибуты recordset read-only, патчить можно только класс.
            ModelClass = type(self.env[model_name])

            if getattr(ModelClass, _AUDIT_PATCHED_ATTR, False):
                continue

            _patch_write(ModelClass)
            _logger.info('audit.rule: patched write() on %s', model_name)


# ------------------------------------------------------------------
# Standalone patch function (outside the model to keep it clean)
# ------------------------------------------------------------------

def _patch_write(Model):
    """Оборачивает метод Model.write(), чтобы отслеживать 
    изменения в полях и создавать записи в файле audit.log."""

    # Guard на случай прямого вызова _patch_write минуя _patch_models.
    # Без этого повторный вызов обернёт уже патченный write() и даст бесконечную рекурсию.
    if getattr(Model, _AUDIT_PATCHED_ATTR, False):
        return

    original_write = Model.write


    def audited_write(self, vals):
        # Читаем отслеживаемые поля из кеша реестра, чтобы избежать SELECT на каждый write().
        # Кеш инвалидируется в _invalidate_tracked_fields_cache при изменении правил.
        #
        # registry — объект уровня базы данных в Odoo: каждая БД имеет свой экземпляр Registry.
        # registry.__dict__ безопасен для кеша в стандартной конфигурации.
        # Потенциальная гонка в многопоточном режиме: два потока могут одновременно
        # увидеть tracked_fields=None и оба выполнят SELECT. Race benign — оба запишут
        # одинаковое значение, данные не повреждаются. Полная защита через Lock
        # избыточна для этого сценария.
        cache_key = '_audit_tracked_fields_%s' % self._name
        tracked_fields = self.env.registry.__dict__.get(cache_key)
        if tracked_fields is None:
            # sudo() — правила читаются системно: обычный пользователь не имеет прав
            # на audit.rule, но аудит должен работать вне зависимости от его роли.
            rules = self.env['audit.rule'].sudo().search([
                ('model_name', '=', self._name),
                ('active', '=', True),
            ])
            tracked_fields = set()
            for rule in rules:
                tracked_fields.update(rule.field_ids.mapped('name'))
            self.env.registry.__dict__[cache_key] = tracked_fields

        fields_in_vals = tracked_fields & set(vals.keys())

        old_values = {}
        record_names = {}
        if fields_in_vals:
            # Захватываем имена записей и старые значения ДО write.
            # display_name может зависеть от изменяемых полей — после write он будет уже новым.
            for record in self:
                record_names[record.id] = _safe_display_name(record)
                old_values[record.id] = {
                    fname: _get_display_value(record, fname)
                    for fname in fields_in_vals
                }

        result = original_write(self, vals)

        if fields_in_vals:
            try:
                _create_log_entries(self, vals, old_values, record_names, fields_in_vals)
            except Exception as e:
                # Ошибка логирования не должна откатывать бизнес-операцию.
                _logger.error(
                    'audit: не удалось записать лог для %s: %s',
                    self._name, e,
                )

        return result

    Model.write = audited_write
    setattr(Model, _AUDIT_PATCHED_ATTR, True)


def _get_display_value(record, field_name):
    """Return a human-readable string for a field value."""
    field = record._fields.get(field_name)
    if field is None:
        return None

    value = record[field_name]

    if field.type == 'many2one':
        return value.display_name if value else False
    if field.type == 'selection':
        return dict(field._description_selection(record.env)).get(value, value)
    if hasattr(value, 'display_name'):
        return value.display_name

    return value


def _create_log_entries(records, vals, old_values, record_names, tracked_fields):
    """Bulk-create audit.log entries for all changed fields across all records."""
    AuditLog = records.env['audit.log']
    user = records.env.user
    user_id = user.id
    user_name = user.name or ''

    field_labels = {
        fname: records._fields[fname].string
        for fname in tracked_fields
        if fname in records._fields
    }

    log_vals = []
    for record in records:
        record_name = record_names.get(record.id, str(record.id))
        for fname in tracked_fields:
            old_val = old_values.get(record.id, {}).get(fname)
            new_raw = vals.get(fname)
            new_val = _resolve_new_value(record, fname, new_raw)

            if old_val == new_val:
                continue

            log_vals.append({
                'model_name': records._name,
                'record_id': record.id,
                'record_name': record_name,
                'field_name': fname,
                'field_description': field_labels.get(fname, fname),
                'old_value': str(old_val) if old_val is not None else False,
                'new_value': str(new_val) if new_val is not None else False,
                'user_id': user_id,
                'user_name': user_name,
            })

    if log_vals:
        AuditLog.sudo().create(log_vals)


def _resolve_new_value(record, field_name, raw_value):
    """Resolve the new value the same way we resolve old values (display-friendly)."""
    field = record._fields.get(field_name)
    if field is None:
        return raw_value

    if field.type == 'many2one' and raw_value:
        related = record.env[field.comodel_name].browse(raw_value)
        return related.display_name if related.exists() else raw_value
    if field.type == 'selection':
        return dict(field._description_selection(record.env)).get(raw_value, raw_value)

    return raw_value


def _safe_display_name(record):
    try:
        return record.display_name
    except Exception as e:
        _logger.debug(
            'audit: не удалось получить display_name для %s#%s: %s',
            record._name, record.id, e,
        )
        return str(record.id)
