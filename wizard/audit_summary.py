from odoo import api, fields, models


class AuditSummaryRecordLine(models.TransientModel):
    """Строка: наиболее изменяемые записи."""

    _name = 'audit.summary.record.line'
    _description = 'Audit Summary — Record Line'

    summary_id = fields.Many2one(comodel_name='audit.summary', ondelete='cascade')
    model_name = fields.Char(string='Модель')
    record_id = fields.Integer(string='ID записи')
    record_name = fields.Char(string='Запись')
    change_count = fields.Integer(string='Кол-во изменений')


class AuditSummaryUserLine(models.TransientModel):
    """Строка: наиболее активные пользователи."""

    _name = 'audit.summary.user.line'
    _description = 'Audit Summary — User Line'

    summary_id = fields.Many2one(comodel_name='audit.summary', ondelete='cascade')
    user_id = fields.Many2one(comodel_name='res.users', string='Пользователь')
    change_count = fields.Integer(string='Кол-во изменений')


class AuditSummary(models.TransientModel):
    _name = 'audit.summary'
    _description = 'Сводный отчёт аудита'

    date_from = fields.Date(string='С')
    date_to = fields.Date(string='По')
    result_limit = fields.Integer(string='Топ N', default=20)
    total_changes = fields.Integer(string='Всего изменений', readonly=True)
    record_line_ids = fields.One2many(
        comodel_name='audit.summary.record.line',
        inverse_name='summary_id',
        string='Наиболее изменяемые записи',
        readonly=True,
    )
    user_line_ids = fields.One2many(
        comodel_name='audit.summary.user.line',
        inverse_name='summary_id',
        string='Наиболее активные пользователи',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Public action
    # ------------------------------------------------------------------

    def action_generate(self):
        self.ensure_one()
        self._clear_lines()

        conditions, params = self._build_where_clause()

        limit = max(1, self.result_limit or 20)
        self.total_changes = self._fetch_total(conditions, params)
        record_rows = self._fetch_top_records(conditions, params, limit=limit)
        user_rows = self._fetch_top_users(conditions, params, limit=limit)

        self._populate_record_lines(record_rows)
        self._populate_user_lines(user_rows)

        # Возвращаем ту же форму — пользователь видит результат без перехода
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # SQL helpers
    # ------------------------------------------------------------------

    def _build_where_clause(self):
        """Возвращает (conditions, params) — conditions содержит только хардкоженные строки.
        params — tuple, чтобы исключить случайную мутацию при передаче в несколько методов."""
        conditions = []
        params = []

        if self.date_from:
            conditions.append('create_date >= %s')
            params.append(self.date_from)
        if self.date_to:
            # date_to включительно — берём конец дня
            conditions.append("create_date < %s + INTERVAL '1 day'")
            params.append(self.date_to)

        return conditions, tuple(params)

    @staticmethod
    def _build_query(select, conditions, tail=None):
        """Собирает SQL из частей без конкатенации строк.
        conditions — только хардкоженные строки, пользовательский ввод идёт через params."""
        parts = [select]
        if conditions:
            parts.append('WHERE ' + ' AND '.join(conditions))
        if tail:
            parts.append(tail)
        return '\n'.join(parts)

    def _fetch_total(self, conditions, params):
        """Общее количество изменений за период."""
        query = self._build_query(
            'SELECT COUNT(*) FROM audit_log',
            conditions,
        )
        self.env.cr.execute(query, params)
        return self.env.cr.fetchone()[0]

    def _fetch_top_records(self, conditions, params, limit=20):
        """Топ записей по количеству изменений."""
        query = self._build_query(
            """SELECT model_name,
                      record_id,
                      MAX(record_name) AS record_name,
                      COUNT(*)         AS change_count
                 FROM audit_log""",
            conditions,
            'GROUP BY model_name, record_id ORDER BY change_count DESC LIMIT %s',
        )
        self.env.cr.execute(query, params + (limit,))
        return self.env.cr.fetchall()

    def _fetch_top_users(self, conditions, params, limit=20):
        """Топ пользователей по количеству изменений."""
        query = self._build_query(
            """SELECT user_id,
                      COUNT(*) AS change_count
                 FROM audit_log""",
            conditions,
            'GROUP BY user_id ORDER BY change_count DESC LIMIT %s',
        )
        self.env.cr.execute(query, params + (limit,))
        return self.env.cr.fetchall()

    # ------------------------------------------------------------------
    # Line population
    # ------------------------------------------------------------------

    def _populate_record_lines(self, rows):
        RecordLine = self.env['audit.summary.record.line']
        RecordLine.create([
            {
                'summary_id': self.id,
                'model_name': row[0],
                'record_id': row[1],
                'record_name': row[2] or str(row[1]),
                'change_count': row[3],
            }
            for row in rows
        ])

    def _populate_user_lines(self, rows):
        UserLine = self.env['audit.summary.user.line']
        UserLine.create([
            {
                'summary_id': self.id,
                'user_id': row[0],
                'change_count': row[1],
            }
            for row in rows
        ])

    def _clear_lines(self):
        """Очищаем предыдущий результат перед повторной генерацией."""
        self.record_line_ids.unlink()
        self.user_line_ids.unlink()
        self.total_changes = 0
