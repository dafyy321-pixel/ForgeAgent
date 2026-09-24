import EmbeddedPostgres from 'embedded-postgres';
import {existsSync, mkdirSync} from 'node:fs';
mkdirSync('.forge', {recursive:true});
const pg = new EmbeddedPostgres({databaseDir:'.forge/postgres',user:'postgres',password:'forge-admin-local',
  port:55432,persistent:true,postgresFlags:['-h','127.0.0.1'],onLog:()=>{},onError:m=>process.stderr.write(String(m)+'\n')});
if(!existsSync('.forge/postgres/PG_VERSION')) await pg.initialise();
await pg.start();
const client=pg.getPgClient();await client.connect();
if(!(await client.query("SELECT 1 FROM pg_roles WHERE rolname='forge'")).rowCount)
  await client.query("CREATE ROLE forge LOGIN PASSWORD 'forge-local' NOSUPERUSER NOBYPASSRLS");
if(!(await client.query("SELECT 1 FROM pg_database WHERE datname='forge'")).rowCount)
  await client.query('CREATE DATABASE forge OWNER forge');
await client.end();
console.log('PostgreSQL ready on 127.0.0.1:55432. Data: .forge/postgres');
for(const signal of ['SIGINT','SIGTERM']) process.on(signal,async()=>{await pg.stop();process.exit(0)});
setInterval(()=>{},60000);

