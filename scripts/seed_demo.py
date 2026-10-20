"""Install the included review demo into a chosen local workspace.

This imports fixed test data through the same Registry used by studio and MCP.
It does not generate, render or accept a video.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.contracts import ProjectBrief
from app.operations import ProductionScript,save_json
from app.registry import Registry

def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True)
    args=p.parse_args();root=args.workspace.resolve();root.mkdir(parents=True,exist_ok=True)
    source=json.loads((ROOT/'examples/mushoku/script.json').read_text(encoding='utf-8-sig'))
    assets=root/'assets';assets.mkdir(exist_ok=True)
    for name in ('landscape.png','study.png','doorway.png'):
        destination=assets/name
        if not destination.exists():shutil.copy2(ROOT/'examples/mushoku/media'/name,destination)
    note=('Проверка завязки произведения, 16 октября 2026 года.\n'
          'Это исследовательская карточка, а не полный текст новеллы.\n'
          'Источник: https://sevenseasentertainment.com/books/mushoku-tensei-jobless-reincarnation-light-novel-vol-1/\n'
          'Аннотация официального издателя сообщает: взрослый затворник погибает, перерождается как Рудеус Грейрат '
          'в мире меча и магии, сохраняет память прежней жизни и намеревается не повторять ошибок.\n'
          'Авторская публикация веб-новеллы: https://ncode.syosetu.com/n9669bk/\n'
          'Проверено существование публикации; главы не прочитаны. Веб-новелла и ранобэ — разные редакции.\n'
          'Визуалы — вновь сгенерированные иллюстрации. Они не доказывают канонические события.\n')
    (assets/'source_note.txt').write_text(note,encoding='utf-8')
    script={'schema_version':'1.0','project_id':source['project_id'],'title':source['title'],
            'scope':source['scope'],'heading':'РЕИНКАРНАЦИЯ БЕЗРАБОТНОГО  /  ПИЛОТ',
            'footer':'Авторская иллюстрация • анализ завязки','segments':[]}
    for block in source['segments']:
        script['segments'].append({'id':block['id'],'text':block['text'],'title':block['title'],
            'pronunciation_text':block['text'].replace('Рудеус','Ру́деус').replace('Грейрат','Гре́йрат'),
            'visual_asset_id':Path(block['image']).stem,'visual_purpose':'illustration',
            'source_asset_ids':['publisher_note'] if block.get('source_id') else [],
            'interpretation':block['claim_type']!='fact'})
    script=ProductionScript.model_validate(script).model_dump()
    save_json(assets/'script_v1.json',script)
    brief=ProjectBrief(project_id=source['project_id'],title=source['title'],work_title='Реинкарнация безработного',
        character='Рудеус Грейрат',source_status='partial',limitations=['Full novel text has not been imported or analyzed.','User acceptance and human listening are pending.'])
    save_json(root/'brief.json',brief.model_dump())
    with Registry(root) as registry:
        registry.initialize(brief)
        for identifier,path,kind in [('landscape','assets/landscape.png','image'),('study','assets/study.png','image'),
            ('doorway','assets/doorway.png','image'),('publisher_note','assets/source_note.txt','text'),('script_v1','assets/script_v1.json','text')]:
            revision=registry.status(source['project_id'])['revision']
            registry.register_asset(source['project_id'],identifier,path,kind=kind,rights='owned',
                                    base_revision=revision,idempotency_key='seed-'+identifier)
        print(json.dumps(registry.status(source['project_id']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
