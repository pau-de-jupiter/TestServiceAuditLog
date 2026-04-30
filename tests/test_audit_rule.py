from odoo.tests.common import TransactionCase
from odoo.addons.audit_log.models.audit_rule import _AUDIT_PATCHED_ATTR


class TestAuditRule(TransactionCase):

    def setUp(self):
        super().setUp()
        # Создаём правило на res.partner — стандартная модель, всегда доступна
        self.model = self.env['ir.model'].search([('model', '=', 'res.partner')], limit=1)
        self.field_name_field = self.env['ir.model.fields'].search([
            ('model_id', '=', self.model.id),
            ('name', '=', 'name'),
        ], limit=1)
        self.field_phone_field = self.env['ir.model.fields'].search([
            ('model_id', '=', self.model.id),
            ('name', '=', 'phone'),
        ], limit=1)

        self.rule = self.env['audit.rule'].create({
            'name': 'Test rule — res.partner',
            'model_id': self.model.id,
            'field_ids': [(6, 0, [self.field_name_field.id, self.field_phone_field.id])],
        })

    # ------------------------------------------------------------------
    # Патч-механизм
    # ------------------------------------------------------------------

    def test_write_is_patched(self):
        """write() модели должен быть обёрнут после создания правила."""
        Model = self.env['res.partner']
        self.assertTrue(
            getattr(Model, _AUDIT_PATCHED_ATTR, False),
            'write() не был запатчен после создания audit.rule',
        )

    def test_no_double_patch(self):
        """Повторный вызов _patch_models не должен двойно оборачивать write()."""
        original = self.env['res.partner'].write
        self.rule._patch_models()
        self.assertEqual(
            self.env['res.partner'].write, original,
            'write() был обёрнут повторно',
        )

    # ------------------------------------------------------------------
    # Запись лога
    # ------------------------------------------------------------------

    def test_change_creates_log(self):
        """Изменение отслеживаемого поля создаёт запись в audit.log."""
        partner = self.env['res.partner'].create({'name': 'Test Partner'})
        self.env['audit.log'].search([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
        ]).unlink()

        partner.write({'name': 'New Name'})

        log = self.env['audit.log'].search([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
            ('field_name', '=', 'name'),
        ])
        self.assertEqual(len(log), 1)
        self.assertEqual(log.old_value, 'Test Partner')
        self.assertEqual(log.new_value, 'New Name')

    def test_untracked_field_no_log(self):
        """Изменение неотслеживаемого поля не создаёт запись."""
        partner = self.env['res.partner'].create({'name': 'Test Partner'})
        before = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
        ])

        partner.write({'email': 'test@example.com'})

        after = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
        ])
        self.assertEqual(before, after, 'Лог создан для неотслеживаемого поля email')

    def test_no_log_if_value_unchanged(self):
        """Запись не создаётся если новое значение равно старому."""
        partner = self.env['res.partner'].create({'name': 'Same Name'})
        count_before = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
            ('field_name', '=', 'name'),
        ])

        partner.write({'name': 'Same Name'})

        count_after = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
            ('field_name', '=', 'name'),
        ])
        self.assertEqual(count_before, count_after)

    def test_log_captures_user(self):
        """Лог сохраняет user_id и user_name текущего пользователя."""
        partner = self.env['res.partner'].create({'name': 'Partner'})
        partner.write({'phone': '+7999'})

        log = self.env['audit.log'].search([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
            ('field_name', '=', 'phone'),
        ], limit=1)

        self.assertEqual(log.user_id.id, self.env.uid)
        self.assertTrue(log.user_name)

    def test_record_name_captured_before_write(self):
        """record_name в логе — имя ДО изменения, не после."""
        partner = self.env['res.partner'].create({'name': 'Old Name'})
        partner.write({'name': 'New Name'})

        log = self.env['audit.log'].search([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
            ('field_name', '=', 'name'),
        ], limit=1, order='create_date desc')

        self.assertEqual(log.record_name, 'Old Name')

    # ------------------------------------------------------------------
    # Инвалидация кеша
    # ------------------------------------------------------------------

    def test_cache_invalidated_on_rule_write(self):
        """После изменения правила кеш tracked_fields сбрасывается."""
        cache_key = '_audit_tracked_fields_res.partner'
        self.env.registry.__dict__[cache_key] = {'name'}

        self.rule.write({'active': True})

        self.assertNotIn(
            cache_key,
            self.env.registry.__dict__,
            'Кеш не был инвалидирован после write на audit.rule',
        )

    def test_cache_invalidated_on_unlink(self):
        """После удаления правила кеш сбрасывается до вызова super().unlink()."""
        cache_key = '_audit_tracked_fields_res.partner'
        self.env.registry.__dict__[cache_key] = {'name'}

        self.rule.unlink()

        self.assertNotIn(cache_key, self.env.registry.__dict__)

    # ------------------------------------------------------------------
    # Деактивация
    # ------------------------------------------------------------------

    def test_deactivated_rule_no_log(self):
        """Деактивированное правило не пишет логи."""
        self.rule.write({'active': False})
        partner = self.env['res.partner'].create({'name': 'Partner'})
        count_before = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
        ])

        partner.write({'name': 'Changed'})

        count_after = self.env['audit.log'].search_count([
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', partner.id),
        ])
        self.assertEqual(count_before, count_after)
