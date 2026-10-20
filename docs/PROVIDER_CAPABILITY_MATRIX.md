# Возможности исполнителей MULTI-AI-V2

Живая установленная возможность и описание в документации — разные свидетельства.
Ни один профиль не включается автоматически по наличию бинарника.

| Роль | Маршрут | Текущее решение | Чего не хватает |
|---|---|---|---|
| Codex Production | Официальный Codex, ChatGPT | VERIFIED для одного JSON smoke | Включение в live production v2 ещё не выполнено |
| Gemini Story | Официальный Antigravity agy | UNAVAILABLE | установленный agy CLI, help, вход, модель и JSON smoke |
| Gemini Visual / Cursor | Официальный Cursor Agent CLI | BLOCKED_UNSUPPORTED локально | установленный Agent CLI; billing и Google-identity не доказаны |
| Gemini Visual / второй Antigravity | Второй официальный профиль | UNVERIFIED, выключен | документированная граница профилей и независимые identity/quota |
| Fake Story/Visual/Codex | FakeAdapter | Только симуляция | Не подтверждает живые модели или просмотр MP4 |

Cursor документирует headless и JSON, поэтому общего утверждения «Cursor не умеет
CLI» нет. Локальный route недоступен; даже после установки нужны проверка тарифа,
модели и media transport. Google AI Pro не считается автоматически оплатой Cursor.
Автоматическая ротация запрещена, роли фиксированы. Если квота недоступна — null.
Ни один способ не получает VERIFIED только из --help.

Итоговые probe JSON: reports/multi_ai_v2/evidence/ (timestamp-каталоги).

Текущая проверка: evidence/20261020T131633Z/codex_probe.json, CLI
0.163.0-alpha.10.2, ChatGPT identity verified (только hash), exit=0,
ответ MULTI-AI-V2-OK; один запрос, input_tokens=11408, output_tokens=38.
Requested/returned model — null: CLI не сообщил фактическое имя.
Gemini Story/Visual — без модельных запросов; registry runners=0;
Google Drive connector появился во время работы: upload/readback маленького
manifest VERIFIED; доступ unattended runner и local mount root ещё не подтверждены.
