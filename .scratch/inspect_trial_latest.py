import asyncio, os, json
from dotenv import load_dotenv
import asyncpg
load_dotenv(r'C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env')
async def main():
 p=await asyncpg.create_pool(os.environ['DATABASE_URL'])
 try:
  out={}
  for t in ['ingestion_runs','ingestion_contexts','ingested_artifacts','artifact_blocks','observations','knowledge_nodes','procedures','evidence']:
   try: out[t]=await p.fetchval(f'SELECT count(*) FROM {t}')
   except Exception as e: out[t]=f'{type(e).__name__}: {e}'
  out['trial_artifacts']=[dict(r) for r in await p.fetch("SELECT id, source_type, uri, path, content_hash, procedure_id, procedure_row_id, run_id FROM ingested_artifacts WHERE run_id=$1::uuid",'1f607644-f48b-4a39-afcf-dc88315f69c9')]
  out['trial_runs']=[dict(r) for r in await p.fetch("SELECT run_id, source_spec, metrics FROM ingestion_runs WHERE run_id=$1::uuid",'1f607644-f48b-4a39-afcf-dc88315f69c9')]
  print(json.dumps(out,default=str,indent=2))
 finally: await p.close()
asyncio.run(main())
