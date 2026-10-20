"""One-time T07 authoring record, not a second task queue or reusable generator."""
from pathlib import Path
import json
from app.project_memory import note_text, generated_views
from app.development import Journal

ROOT = Path.cwd()
V = ROOT / 'knowledge'
RUN = 'reports/development/2026-10-17_MAP'
for folder in ['areas','capabilities','decisions','ideas','templates','maps','generated/code']:
    (V/folder).mkdir(parents=True, exist_ok=True)

def write(ref, text):
    path=V/ref
    with path.open('x',encoding='utf-8',newline='\n') as out: out.write(text)

def props(identifier, kind, title, area=None, **extra):
    return dict(schema_version=1,id=identifier,kind=kind,title=title,area=area,parent=area,
        implementation='partial',verification='not_checked',verified_at=None,verified_revision=None,
        code_refs=[],evidence_refs=[],requires=[],related=[],task_ids=[],**extra)

areas = [
 ('SRC','Источники','Sources','#d49a57','Материалы, цитаты, события и поиск.'),
 ('SCR','Сценарий','Script','#b593d3','Тема, аргументация, блоки и редактура.'),
 ('AUD','Аудио','Audio','#65bba9','Ruslan, произношение, WAV и контроль речи.'),
 ('VIS','Визуалы','Visuals','#78a7d5','Подбор изображений и подготовка кадров.'),
 ('EDT','Монтаж','Editing','#db9494','Timeline, движения, титры и экспорт.'),
 ('QA','Качество','Quality','#b2be6b','Факты, звук, технические проверки и передача.'),
 ('OPS','Управление','Operations','#a0a9b9','Профиль, задания, контекст и карты.')]

# ID, title, code, tasks, implemented portion, explicit limitation, next step
cards = [
 ('SRC-IMPORT','Импорт материалов','app/library.py',[], 'Library.import_source регистрирует TXT/EPUB и фрагменты.', 'Полный импорт и анализ новеллы в производстве не подтверждён.', 'Выбирать нужные источники по заданию владельца.'),
 ('SRC-CLAIMS','События и доказательства','app/contracts.py app/library.py',[], 'Контракт Claim связывает утверждения с SourceRef; Library проверяет ссылки.', 'Корректная схема не подтверждает истинность утверждения.', 'Сверять конкретное утверждение с источником.'),
 ('SRC-DOSSIER','Досье по теме','app/contracts.py',[], 'Контракт Dossier задаёт структуру тематического досье.', 'Автоматический полный анализ книги не подтверждён.', 'Составлять досье только по выбранной теме.'),
 ('SRC-SEARCH','Поиск по источникам','app/library.py',[], 'Library.search выдаёт ограниченный поиск по зарегистрированным материалам.', 'Поиск не заменяет проверку цитаты и полноты корпуса.', 'Проверить найденный фрагмент через Library.read.'),
 ('SCR-BRIEF','Тема и замысел','app/contracts.py',[], 'ProjectBrief описывает тему, цель и параметры выпуска.', 'Тему и художественный замысел утверждает человек.', 'Согласовать brief до производства.'),
 ('SCR-STRUCTURE','Аргументация сценария','app/contracts.py',[], 'ScriptBlock и Dossier дают структуры для сценарного решения ИИ и человека.', 'Автономная редактура не подтверждена.', 'Обосновать тезисы ссылками на источники.'),
 ('SCR-BLOCKS','Блоки сценария','app/operations.py app/contracts.py',[], 'ProductionScript и ProductionBlock описывают текст, визуалы и длительности.', 'Правка блока требует согласованной ревизии проекта.', 'Использовать текущий контракт; не запускать выпуск этим заданием.'),
 ('SCR-REVIEW','Редактура и согласование','app/operations.py app/review.py',[], 'patch_script и read_review поддерживают адресную правку и просмотр результата.', 'Программная проверка не равна редакционному принятию.', 'Зафиксировать решение владельца отдельно.'),
 ('AUD-PROFILE','Диктор Ruslan','app/tts.py app/production_profile.py',['T01'], 'Профиль закрепляет Piper ru_RU-ruslan-medium и технические предпочтения.', 'Индивидуальные fitted takes не становятся глобальной настройкой темпа.', 'Сохранить Ruslan; проверять профиль перед новым производством.'),
 ('AUD-PRONUNCIATION','Имена и произношение','app/tts.py app/production_profile.py',['T01'], 'Профиль содержит словарь имён, TTS применяет настройки произношения.', 'Правильное написание не доказывает слуховое качество.', 'Проверять конкретную реплику и слушать окончания.'),
 ('AUD-WAV','Отдельные реплики WAV','app/tts.py app/operations.py',[], 'synthesize создаёт блоки WAV и манифест; кеш хранит готовые реплики.', 'Кеш и исходные WAV сохраняются; новый синтез здесь не выполнялся.', 'Переиспользовать проверенный блок при совпадении входов.'),
 ('AUD-ASR','Распознавание речи','app/speech_review.py scripts/transcribe_audio.py',['T04'], 'review_build_speech и transcribe_manifest выполняют распознавание и проверку.', 'T04 pending: ключ ASR и сверка ожидаемого текста ещё не разделены; не обещать повторное использование после визуальных правок.', 'Отдельное поручение T04: разделить кеш распознавания и соответствие, с подменённым распознавателем.'),
 ('AUD-ALIGN','Соответствие речи и тайминги','scripts/transcribe_audio.py app/speech_review.py',['T04'], 'align_script_words связывает слова с временными метками и субтитрами.', 'ASR может пропускать отрицания и окончания; T04 остаётся pending.', 'Проверять спорные слова и тайминги вручную.'),
 ('AUD-REPLACE','Замена звука','scripts/fit_narration.py scripts/replace_narration.py',['T05','T06'], 'Отдельные fit_narration и replace_narration сохраняют путь замены звука.', 'T05 pending: штатной операции очереди нет. T06 pending: QA/package второго выпуска предшествуют финальной замене Ruslan.', 'Отдельное поручение T05–T06; текущие файлы и ручной MLT сохранить.'),
 ('AUD-MIX','Музыка и баланс','app/media.py app/timeline.py',[], 'original_music и Timeline описывают музыкальную дорожку и сборку звука.', 'Автоматический баланс не заменяет прослушивание.', 'Проверять уровни на актуальной версии.'),
 ('AUD-CHARACTERS','Голоса персонажей','',[], 'Предложение о нескольких персонажных голосах.', 'Будущая возможность; не заменяет Ruslan и не разрешает установку голосов.', 'Обсуждать только по отдельному поручению.'),
 ('VIS-CATALOG','Архив визуалов','app/registry.py app/contracts.py',[], 'Реестр хранит активы и ссылки на исходники.', 'Импорт всего архива и полная семантическая каталогизация не подтверждены.', 'Регистрировать только выбранные материалы.'),
 ('VIS-SEARCH','Поиск визуалов','app/registry.py',[], 'Активы доступны через реестр; выбор выполняет человек/ИИ.', 'Автоматический смысловой поиск по всему архиву не подтверждён.', 'Сформулировать критерии и проверить права на материал.'),
 ('VIS-MATCH','Соответствие кадра тексту','app/contracts.py app/operations.py',[], 'ShotPlan и ProductionVisualShot задают выбор и длительность кадра.', 'Семантическое соответствие оценивают ИИ и человек.', 'Сверить выбранный кадр с репликой.'),
 ('VIS-PREP','Подготовка кадров','app/media.py app/timeline.py',[], 'framing_filter и staging готовят кадр и переносимые ресурсы проекта.', 'Исходники и ручные кадрирования нельзя перезаписывать.', 'Создавать производные материалы отдельно.'),
 ('EDT-TIMELINE','Таймлайн проекта','app/timeline.py app/contracts.py',[], 'build_timeline и build_project формируют Timeline и MLT.', 'Сохранять ручные правки и связи ресурсов.', 'Проверять проект на коротких временных данных.'),
 ('EDT-MOTION','Движение в кадре','app/media.py app/timeline.py',[], 'camera_filter задаёт движение и кадрирование.', 'Художественную уместность определяет просмотр.', 'Согласовывать движение с содержанием сцены.'),
 ('EDT-TITLES','Титры и субтитры','app/timeline.py scripts/transcribe_audio.py',[], 'MLT и write_srt поддерживают титры и временные подписи.', 'Читаемость и попадание слов требуют визуальной проверки.', 'Проверять короткий участок с проблемными титрами.'),
 ('EDT-EXPORT','Проект и экспорт видео','app/operations.py app/timeline.py',[], 'build/render формируют проект и видео через существующие инструменты.', 'Полный рендер этим заданием запрещён.', 'Использовать существующие операции только по производственному поручению.'),
 ('QA-FACTS','Проверка фактов','app/contracts.py app/library.py',[], 'SourceRef и validate_ref связывают утверждения с материалами.', 'Наличие ссылки не доказывает смысловую верность.', 'Проверить факт по конкретному фрагменту.'),
 ('QA-SOUND','Проверка звука','app/speech_review.py app/operations.py',[], 'Речевая сверка и qa проверяют аудио и метрики.', 'Неизвестное прослушивание не считается пройденным.', 'Слушать отрицания, имена, окончания и баланс.'),
 ('QA-TECH','Технические проверки','app/operations.py app/contracts.py',[], 'qa проверяет параметры, длительности, декодирование и зависимости.', 'done задачи не означает passed всех проверок.', 'Читать полный QA актуального артефакта.'),
 ('QA-DELIVERY','Готовность и передача','app/operations.py app/review.py',['T06'], 'package и read_review существуют для зарегистрированных результатов.', 'T06 pending: финальная замена Ruslan вне актуальной QA/package цепочки; ready_for_review не равно accepted.', 'Отдельное поручение T06: привязать пакет к проверенному финальному результату.'),
 ('OPS-PROFILE','Профиль и режимы','app/production_profile.py',['T01'], 'Проверка профиля и runtime отделяет предпочтения от локальных путей.', 'Профиль не менять без отдельной development-задачи.', 'Использовать --check-runtime перед производством.'),
 ('OPS-JOBS','Задания и кеш','app/jobs.py app/registry.py app/exchange.py',['T02','T03'], 'JobManager и Exchange используют существующую очередь и реестр.', 'Журнал развития отдельно от jobs; timeout не отменяет работу.', 'Читать job по ID; не создавать вторую очередь.'),
 ('OPS-CONTEXT','Контекст по узлу','app/development.py',['T10'], 'Журнал T02 и короткий обмен T03 дают основу для адаптера контекста.', 'До проверки T10 контекст по карточке не объявлен готовым.', 'Выполнить разрешённую T10 и сохранить доказательства.'),
 ('OPS-CODEMAP','Карта кода Graphify','',['T09'], 'Запланирован локальный структурный индекс исходников и тестов.', 'Нет модели, облака, hooks и watcher; до T09 граф не проверен.', 'Выполнить T09 в отдельной среде и проверить реальные запросы.'),
 ('OPS-OBSIDIAN','Навигация Obsidian','app/project_memory.py',['T07','T08','T10'], 'Markdown-карточки и стандартные Canvas образуют карту студии.', 'Создание карты не доказывает качество видео; GUI проверяется отдельно.', 'Проверить карту и безопасное сохранение ручных правок.')]

for prefix,title,canvas,color,desc in areas:
    identifier='AREA-'+prefix
    p=props(identifier,'area',title,canvas=f'maps/{canvas}.canvas',color=color)
    children=[c for c in cards if c[0].startswith(prefix+'-')]
    body=f'# {title}\n\n## Кратко\n{desc}\nСостояние: смешанное, см. карточки.\nОграничение: наличие заметки не означает готовую функцию.\nСледующий шаг: выбрать нужную карточку.\n\n[[maps/{canvas}.canvas|Открыть карту «{title}»]]\n\n## Возможности\n'
    body+='\n'.join(f'- [[capabilities/{c[0]}|{c[1]}]]' for c in children)
    body+='\n\n[[Studio_Map.canvas|Главная карта]] · [[START|Начало]]\n'
    write(f'areas/{identifier}.md',note_text(p,body))

for identifier,title,code,tasks,purpose,limit,nxt in cards:
    area='AREA-'+identifier.split('-')[0]
    p=props(identifier,'capability',title,area)
    p.update(code_refs=code.split(),task_ids=tasks)
    if identifier in {'AUD-CHARACTERS','OPS-CODEMAP'}: p['implementation']='planned'
    if identifier in {'AUD-PROFILE','AUD-PRONUNCIATION','OPS-PROFILE'}:
        p.update(implementation='implemented',verification='historical_evidence',evidence_refs=['config/production_profile.json','reports/development/2026-10-17_A/T01_checks.json'])
    if identifier == 'OPS-JOBS':
        p.update(implementation='implemented',verification='historical_evidence',evidence_refs=['reports/development/2026-10-17_A/T03_checks.json'])
    if identifier in {'AUD-REPLACE','QA-DELIVERY'}: p['evidence_refs']=['development/INTERFACES.md','reports/development/2026-10-17_A/production_baseline.json']
    if identifier in {'AUD-ASR','AUD-ALIGN'}: p['related']=['AUD-WAV']; p['evidence_refs']=['development/INTERFACES.md']
    if identifier=='AUD-PRONUNCIATION': p['requires']=['AUD-PROFILE']
    if identifier=='OPS-CODEMAP': p['related']=['OPS-OBSIDIAN']
    if identifier=='OPS-OBSIDIAN': p['related']=['OPS-CODEMAP']
    names={c[0]:c[1] for c in cards}
    body=f'# {title}\n\n## Кратко\n{purpose}\nСостояние: {p["implementation"]}; проверка: {p["verification"]}.\nОграничение: {limit}\nСледующий шаг: {nxt}\n\n## Назначение\n{purpose}\n\n## Входы и выходы\nВход: выбранные материалы и текущие версии по ссылкам. Выход: {title.lower()} в границах описанной реализации; результаты производства остаются в реестре.\n\n## Реализация\n'
    body+='\n'.join(f'- `{ref}` — project-relative; разрешается адаптером context/resolve.' for ref in p['code_refs']) or 'Будущая возможность; код не указан.'
    body+='\n\n## Доказательства\n'
    body+=('\n'.join(f'- `{ref}`' for ref in p['evidence_refs']) or 'Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.')
    body+='\n\n## Связи\n'+f'[[areas/{area}|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]\n'
    body+='\n'.join(f'- [[capabilities/{v}|{names[v]}]]' for v in p['requires']+p['related'])
    body+=f'\n\n## Ограничения\n{limit}\n\n## Будущие улучшения\n{nxt}\n'
    if tasks: body+='\nЗадачи: '+', '.join(tasks)+'. Статус только в [[generated/TASKS|журнале задач]].\n'
    write(f'capabilities/{identifier}.md',note_text(p,body))

p=props('DEC-MAP-ORDER','decision','Разрешён порядок T07 → T08 → T09 → T10',status='accepted')
p.update(implementation='implemented',task_ids=['T07','T08','T09','T10'],code_refs=['app/development.py'],evidence_refs=['development/INTERFACES.md'])
write('decisions/DEC-MAP-ORDER.md',note_text(p,'# Порядок работ по поручению владельца\n\n## Кратко\nПринято 2026-10-17: T07 → T08 → T09 → T10.\nT00–T03 не повторять; T04–T06 оставить pending; T11–T12 не разрешены.\n\n## Основание\nВладелец явно изменил порядок в текущем задании. Проверка исходного плана: T07 технически требует T02; T06 представляла условие «после B». Карта может честно показывать незавершённую B.\n\n## Последствия\nJournal.authorize_map_batch сохраняет прежнюю зависимость в amendment и ограничивает разрешение четырьмя ID. Остальные зависимости и проверки done сохраняются. Один агент; без платных API, синтеза, рендера и изменения TTS.\n\n[[generated/TASKS|Задачи]] · [[START|Начало]]\n'))
j=Journal(ROOT)
j.authorize_map_batch(expected_hash=j.read()[1],operation_key='MAP-owner-order-2026-10-17',decision_ref='knowledge/decisions/DEC-MAP-ORDER.md',run_ref=RUN)
j.update('T07','in_progress',expected_hash=j.read()[1],operation_key='MAP-T07-start')

write('START.md','# AI_VIDEO_02 — студия и карта развития\n\n## Кратко\nРаботающая видеостудия: выбранные материалы → сценарий → Ruslan → монтаж → проверки → просмотр владельцем.\nКарта помогает найти функцию; ничего не запускает сама.\n\n- [[Studio_Map.canvas|Дерево развития]] — выберите одну из семи областей.\n- [[maps/Video_Process.canvas|Как создаётся видео]] — этапы и исполнители.\n- [[generated/STATUS|Что сделано]] · [[generated/TASKS|Что осталось]] · [[generated/HISTORY|История]].\n- [[templates/idea|Новая идея]] — предложение, не исполняемая задача.\n- [[Code_Map|Карта кода]] — локальный Graphify и точечный контекст.\n- [[HOW_TO_USE|Как пользоваться и откатить изменения]].\n\nT00–T03 завершены. Текущий проход T07–T10; T04–T06 остаются незавершёнными.\nПринятый порядок: [[decisions/DEC-MAP-ORDER|решение владельца]].\n')
write('INDEX.md','# Указатель\n\n[[START|Открыть стартовую страницу AI_VIDEO_02]]\n')
write('Code_Map.md','# Карта кода\n\n## Кратко\nЛокальный структурный индекс Graphify. Состояние и команды обновления — в [[HOW_TO_USE|инструкции]].\nИсходники не копируются в vault.\n\nПосле T09 технические заметки находятся в `generated/code/`; граф и HTML — в `graphify-out/` проекта. Пути вне vault разрешает адаптер.\n\nДля обзорного Graph view задайте фильтр `-path:generated/code`.\n[[capabilities/OPS-CODEMAP|Graphify]] · [[START|Начало]]\n')
write('HOW_TO_USE.md','# Как пользоваться\n\nОткройте папку `knowledge` как самостоятельное хранилище Obsidian, затем START.md.\nИнструкция команд будет дополнена после проверки T08–T10.\n\n[[START|Начало]]\n')
for kind in ['area','capability','idea','decision']:
    p=props('NEW-ID',kind,'Название',status='proposed') if kind in {'idea','decision'} else props('NEW-ID',kind,'Название')
    write(f'templates/{kind}.md',note_text(p,'# Название\n\n## Кратко\nЧто делает. Состояние. Ограничение. Следующий шаг.\n\n## Назначение\nУточнить.\n\n## Входы и выходы\nУточнить.\n\n## Реализация\nProject-relative code_refs.\n\n## Доказательства\nНе объявлять функцию проверенной без результата.\n\n## Связи\nVault-relative ссылки; ID стабилен при переименовании title/aliases.\n\n## Ограничения\nЗаметка не создаёт задачу.\n\n## Будущие улучшения\nТолько по отдельному разрешению.\n'))
for ref,text in generated_views(ROOT).items(): write(ref,text)
print(json.dumps({'areas':7,'capabilities':33,'authorization':'T07–T10'},ensure_ascii=False))
