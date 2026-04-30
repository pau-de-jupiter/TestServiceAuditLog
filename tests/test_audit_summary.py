from datetime import date, timedelta

from odoo.tests.common import TransactionCase


class TestAuditSummary(TransactionCase):

    def setUp(self):
        super().setUp()
        # Создаём тестовые логи напрямую, не через патч-механизм
        self.user = self.env.user
        self.env['audit.log'].create([
            {
                'model_name': 'crm.lead',
                'record_id': 1,
                'record_name': 'Lead A',
                'field_name': 'name',
                'field_description': 'Название',
                'old_value': 'Old',
                'new_value': 'New',
                'user_id': self.user.id,
                'user_name': self.user.name,
            },
            {
                'model_name': 'crm.lead',
                'record_id': 1,
                'record_name': 'Lead A',
                'field_name': 'stage_id',
                'field_description': 'Этап',
                'old_value': 'Новый',
                'new_value': 'В работе',
                'user_id': self.user.id,
                'user_name': self.user.name,
            },
            {
                'model_name': 'res.partner',
                'record_id': 5,
                'record_name': 'Partner B',
                'field_name': 'phone',
                'field_description': 'Телефон',
                'old_value': '+7999',
                'new_value': '+7911',
                'user_id': self.user.id,
                'user_name': self.user.name,
            },
        ])

    def _make_wizard(self, date_from=None, date_to=None, limit=20):
        return self.env['audit.summary'].create({
            'date_from': date_from,
            'date_to': date_to,
            'result_limit': limit,
        })

    # ------------------------------------------------------------------

    def test_total_count(self):
        """_fetch_total возвращает корректное общее количество."""
        wizard = self._make_wizard()
        conditions, params = wizard._build_where_clause()
        total = wizard._fetch_total(conditions, params)
        self.assertGreaterEqual(total, 3)

    def test_top_records_returned(self):
        """_fetch_top_records возвращает записи отсортированные по убыванию изменений."""
        wizard = self._make_wizard()
        wizard.action_generate()
        self.assertTrue(wizard.record_line_ids)
        counts = wizard.record_line_ids.mapped('change_count')
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_top_users_returned(self):
        """_fetch_top_users возвращает хотя бы одного пользователя."""
        wizard = self._make_wizard()
        wizard.action_generate()
        self.assertTrue(wizard.user_line_ids)

    def test_date_filter_from(self):
        """Фильтр date_from исключает старые записи."""
        tomorrow = date.today() + timedelta(days=1)
        wizard = self._make_wizard(date_from=tomorrow)
        conditions, params = wizard._build_where_clause()
        total = wizard._fetch_total(conditions, params)
        self.assertEqual(total, 0)

    def test_date_filter_to(self):
        """Фильтр date_to включает записи за текущий день."""
        wizard = self._make_wizard(date_to=date.today())
        conditions, params = wizard._build_where_clause()
        total = wizard._fetch_total(conditions, params)
        self.assertGreaterEqual(total, 3)

    def test_result_limit(self):
        """result_limit ограничивает количество строк в отчёте."""
        wizard = self._make_wizard(limit=1)
        wizard.action_generate()
        self.assertLessEqual(len(wizard.record_line_ids), 1)
        self.assertLessEqual(len(wizard.user_line_ids), 1)

    def test_clear_lines_on_regenerate(self):
        """Повторная генерация очищает старые строки."""
        wizard = self._make_wizard()
        wizard.action_generate()
        count_first = len(wizard.record_line_ids)

        wizard.action_generate()
        count_second = len(wizard.record_line_ids)

        self.assertEqual(count_first, count_second)
