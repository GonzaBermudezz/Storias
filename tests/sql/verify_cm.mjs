import assert from 'node:assert/strict';
import { applyMigrations, createDatabase } from './bootstrap.mjs';

const db = await createDatabase();
const query = async (sql, args = []) => (await db.query(sql, args)).rows;
const scalar = async (sql, args = []) => Object.values((await query(sql, args))[0])[0];
const passed = [];

try {
  await applyMigrations(db);
  // The CM migration must be idempotent: the user may run it twice.
  await db.exec(await (await import('node:fs')).readFileSync('migrations/20261111_cm_agent.sql', 'utf8'));
  passed.push('cm migration applies twice');

  assert.equal(await scalar("SELECT column_default FROM information_schema.columns WHERE table_name='agencies' AND column_name='cm_enabled'"), 'false');
  passed.push('agencies.cm_enabled defaults to false');

  const agency = await scalar("INSERT INTO agencies(name,slug) VALUES ('CM','cm') RETURNING id");
  const client = await scalar("INSERT INTO clients(agency_id,name) VALUES ($1,'Cafe') RETURNING id", [agency]);
  const conversation = await scalar(
    "INSERT INTO cm_conversations(client_id,system_prompt,tools_version) VALUES ($1,'SYS','v1') RETURNING id", [client]);
  const insertMessage = (seq, role = 'user', kind = 'mensaje') => db.query(
    "INSERT INTO cm_messages(conversation_id,client_id,seq,role,kind,content) VALUES ($1,$2,$3,$4,$5,'[]'::jsonb)",
    [conversation, client, seq, role, kind]);
  assert.equal(await scalar("SELECT data_type FROM information_schema.columns WHERE table_name='cm_conversations' AND column_name='busy_token'"), 'uuid');
  passed.push('cm_conversations.busy_token is a uuid');
  await insertMessage(1);
  await assert.rejects(insertMessage(1), /duplicate key/i);
  await assert.rejects(insertMessage(2, 'system'), /check/i);
  await assert.rejects(insertMessage(3, 'user', 'otra'), /check/i);
  passed.push('cm_messages enforces unique seq and role/kind checks');

  await db.query("INSERT INTO cm_pending_actions(conversation_id,client_id,tool_use_id,kind,payload) VALUES ($1,$2,'tu1','descripcion','{}'::jsonb)", [conversation, client]);
  await assert.rejects(
    db.query("INSERT INTO cm_pending_actions(conversation_id,client_id,tool_use_id,kind,payload) VALUES ($1,$2,'tu1','descripcion','{}'::jsonb)", [conversation, client]),
    /duplicate key/i);
  await assert.rejects(
    db.query("INSERT INTO cm_pending_actions(conversation_id,client_id,tool_use_id,kind,payload,status) VALUES ($1,$2,'tu2','descripcion','{}'::jsonb,'raro')", [conversation, client]),
    /check/i);
  assert.equal(await scalar("SELECT status FROM cm_pending_actions WHERE tool_use_id='tu1'"), 'pending');
  passed.push('cm_pending_actions: one row per tool_use_id, valid statuses, pending by default');

  const rls = await query("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('cm_conversations','cm_messages','cm_pending_actions') ORDER BY relname");
  assert.ok(rls.length === 3 && rls.every((row) => row.relrowsecurity === true));
  assert.equal(await scalar("SELECT count(*)::int FROM pg_policies WHERE tablename LIKE 'cm_%'"), 0);
  passed.push('RLS enabled with no policies on the CM tables');

  await db.query('DELETE FROM clients WHERE id=$1', [client]);
  assert.equal(await scalar('SELECT count(*)::int FROM cm_messages'), 0);
  passed.push('deleting a client cascades to its conversations');

  console.log(JSON.stringify({ checks: passed.length, passed }, null, 2));
} finally {
  await db.close();
}
