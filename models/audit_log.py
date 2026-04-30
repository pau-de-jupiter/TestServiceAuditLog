from odoo import fields, models


class AuditLog(models.Model):
    _name = 'audit.log'
    _description = 'Audit Log'
    _order = 'create_date desc'
    _log_access = True

    model_name = fields.Char(string='Model', required=True, index=True)
    # Integer, а не Many2one: поле указывает на разные модели в зависимости от model_name,
    # реальный FK невозможен. Фильтрация всегда идёт по паре model_name + record_id.
    record_id = fields.Integer(string='Record ID', required=True, index=True)
    record_name = fields.Char(string='Record Name')
    field_name = fields.Char(string='Field', required=True)
    field_description = fields.Char(string='Field Label')
    old_value = fields.Text(string='Old Value')
    new_value = fields.Text(string='New Value')
    user_id = fields.Many2one(
        comodel_name='res.users',
        string='User',
        index=True,
        # set null, а не restrict: удаление пользователя не должно блокироваться логами.
        # Имя сохраняется отдельно в user_name — лог остаётся читаемым после удаления юзера.
        ondelete='set null',
    )
    user_name = fields.Char(string='User Name')

    def _auto_init(self):
        super()._auto_init()
        # Составной индекс для запросов таймлайна: WHERE model_name = ? AND record_id = ?
        # Одиночные индексы через index=True уже есть, но составной исключает двойной lookup
        # при самом частом паттерне доступа.
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS audit_log_model_record_idx
                ON audit_log (model_name, record_id);
        """)
