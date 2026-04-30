from collections import defaultdict
from datetime import datetime, timezone

import pytz

from odoo import api, models


class AuditLogMixin(models.AbstractModel):
    """Миксин для встраивания таймлайна аудита в форму любой модели.

    Использование:
        class CrmLead(models.Model):
            _name = 'crm.lead'
            _inherit = ['crm.lead', 'audit.log.mixin']

    Трекинг изменений при этом не включается автоматически —
    он настраивается отдельно через audit.rule в UI.
    """

    _name = 'audit.log.mixin'
    _description = 'Audit Log Mixin'

    # Жёсткий лимит строк на один таймлайн. Без него запись с 50k изменений
    # загрузит все строки в память за один search().
    _AUDIT_LOG_LIMIT = 500

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def get_audit_log_grouped(self, record_id):
        """Возвращает лог изменений для записи, сгруппированный по дате.

        Формат ответа:
        [
            {
                'date': '29 апреля 2026',
                'entries': [
                    {
                        'time':      '14:32',
                        'user':      'Иван Иванов',
                        'field':     'Статус',
                        'old_value': 'Новый',
                        'new_value': 'В работе',
                    },
                    ...
                ],
            },
            ...
        ]
        """
        logs = self._fetch_logs(record_id)
        return self._group_by_date(logs)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_logs(self, record_id):
        return (
            self.env['audit.log']
            .sudo()
            .search(
                [('model_name', '=', self._name), ('record_id', '=', record_id)],
                order='create_date desc',
                limit=self._AUDIT_LOG_LIMIT,
            )
        )

    def _group_by_date(self, logs):
        """Группирует записи лога по календарной дате в timezone пользователя."""
        groups = defaultdict(list)
        ordered_dates = []

        # "Сегодня" считаем тоже в timezone пользователя, а не серверном UTC
        user_today = self._to_user_date(datetime.now(tz=timezone.utc))

        for log in logs:
            log_date = self._to_user_date(log.create_date)
            if log_date not in groups:
                ordered_dates.append(log_date)
            groups[log_date].append(self._format_entry(log))

        return [
            {
                'date': self._format_date_label(d, user_today),
                'entries': groups[d],
            }
            for d in ordered_dates
        ]

    def _format_entry(self, log):
        return {
            'time': self._to_user_time(log.create_date),
            # user_name хранит имя на момент записи — корректно показывает удалённых пользователей
            'user': log.user_name or log.user_id.name or '',
            'field': log.field_description or log.field_name,
            'old_value': log.old_value or '—',
            'new_value': log.new_value or '—',
        }

    def _to_user_date(self, dt):
        """Конвертирует UTC datetime в date пользователя через контекстный timezone."""
        return self._localize_dt(dt).date()

    def _to_user_time(self, dt):
        return self._localize_dt(dt).strftime('%H:%M')

    def _localize_dt(self, dt):
        """Применяет timezone из контекста или UTC как fallback."""
        tz_name = self.env.context.get('tz') or self.env.user.tz or 'UTC'
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.utc
        # Явно снимаем tzinfo перед локализацией: метод принимает как naive (Odoo store),
        # так и aware datetime (datetime.now(tz=utc) из _group_by_date).
        # replace(tzinfo=None) в обоих случаях даёт корректный naive UTC для pytz.localize.
        naive_utc = dt.replace(tzinfo=None)
        return pytz.utc.localize(naive_utc).astimezone(tz)

    @staticmethod
    def _format_date_label(d, user_today):
        """Возвращает читаемую метку даты: 'Сегодня', 'Вчера' или '28 апреля 2026'."""
        delta = (user_today - d).days
        if delta == 0:
            return 'Сегодня'
        if delta == 1:
            return 'Вчера'
        # f-string вместо %-d: %-d не работает на macOS и Windows
        return f"{d.day} {d.strftime('%B %Y')}"
