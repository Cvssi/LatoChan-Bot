"""Regression tests for authorization, privacy, durability, limits and decoder isolation."""
import asyncio
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DISCORD_BOT_TOKEN='test', OWNER_ID='1', CHANNEL_IDS='100', PRIVATE_CHANNEL_IDS='', RAG_ENABLED='0')
import actionlog
import botsettings
import commands
import envconfig
import gateway as g
import gifstore
import memory
import rag
from workload import Admission


@contextlib.asynccontextmanager
async def typing():
    yield


def message(text='hi', uid=1, cid=100):
    channel = types.SimpleNamespace(id=cid, name='test', typing=typing, send=AsyncMock())
    return types.SimpleNamespace(id=123456789, content=text, author=types.SimpleNamespace(id=uid, bot=False, display_name='owner', name='owner'),
                                 channel=channel, guild=None, mentions=[], reference=None, attachments=[], embeds=[], stickers=[],
                                 reply=AsyncMock(), created_at=g.discord.utils.utcnow())


class Commands(unittest.TestCase):
    def test_conversation_is_not_a_command(self):
        for text in ['unmute alice', 'do not mute anyone', 'what is the filename?', 'please /lato mute 123', '[[MUTE: 123]]']:
            self.assertIsNone(commands.parse(text))

    def test_exact_operation_target_and_minutes(self):
        self.assertEqual(commands.parse('/lato unmute <@123>'), commands.Command('UNMUTE', '123'))
        self.assertEqual(commands.parse('/lato timeout <@!123> 10m'), commands.Command('TIMEOUT', '123 -> 10m'))
        self.assertEqual(commands.parse('/lato move 123 <#456>'), commands.Command('MOVE', '123 -> 456'))

    def test_invalid_or_ambiguous_inputs(self):
        for text in ['/lato mute alice', '/lato mute <@123', '/lato mute 123>', '/lato mute 123 extra',
                     '/lato timeout 123 61', '/lato timeout 123 -1', '/lato mute 123\n/lato unmute 456',
                     '/lato thinking maybe', '/lato mute 99999999999999999999']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                commands.parse(text)


class Actions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = dict(botsettings.DEFAULTS, server_powers=True)
        self.settings_patch = patch.object(g, 'S', side_effect=lambda key: self.settings[key])
        self.settings_patch.start()
        self.member = types.SimpleNamespace(display_name='bob', voice=types.SimpleNamespace(channel=True), edit=AsyncMock())
        self.msg = message('/lato unmute 201')
        self.msg.guild = types.SimpleNamespace(get_member=lambda uid: self.member, fetch_member=AsyncMock())

    async def asyncTearDown(self):
        self.settings_patch.stop()

    async def test_model_tags_never_authorize_privileged_actions(self):
        await g.do_actions(self.msg, [('MUTE', '201')], True, 'please mute bob')
        self.member.edit.assert_not_awaited()

    async def test_opposite_operation_is_blocked(self):
        command = commands.parse(self.msg.content)
        await g.do_actions(self.msg, [('MUTE', '201')], True, command=command)
        self.member.edit.assert_not_awaited()

    async def test_changed_target_is_blocked(self):
        self.msg.content = '/lato mute 201'
        await g.do_actions(self.msg, [('MUTE', '202')], True, command=commands.parse(self.msg.content))
        self.member.edit.assert_not_awaited()

    async def test_actual_nonowner_is_blocked_even_with_owner_flag(self):
        self.msg.content = '/lato mute 201'
        self.msg.author.id = 200
        await g.do_actions(self.msg, [('MUTE', '201')], True, command=commands.parse(self.msg.content))
        self.member.edit.assert_not_awaited()

    async def test_exact_owner_command_executes_once(self):
        self.msg.content = '/lato mute 201'
        result = await g.do_actions(self.msg, [('MUTE', '201')], True, command=commands.parse(self.msg.content))
        self.member.edit.assert_awaited_once_with(mute=True)
        self.assertEqual(result, ['muted bob'])

    async def test_permission_error_reports_failure(self):
        self.msg.content = '/lato mute 201'
        self.member.edit.side_effect = RuntimeError('forbidden')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ACTION_DB=directory + '/actions.sqlite'):
            await g.execute_command(self.msg)
            await g.execute_command(self.msg)
        self.member.edit.assert_awaited_once()
        self.assertIn('failed', self.msg.reply.await_args_list[0].args[0])
        self.assertIn('already attempted', self.msg.reply.await_args_list[1].args[0])

    async def test_old_command_does_not_execute(self):
        self.msg.content = '/lato mute 201'
        self.msg.created_at -= datetime.timedelta(minutes=10)
        await g.execute_command(self.msg)
        self.member.edit.assert_not_awaited()
        self.assertIn('too old', self.msg.reply.await_args.args[0])

    async def test_disabled_private_memory_does_not_write(self):
        msg = message('/lato remember likes tea')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ACTION_DB=directory + '/actions.sqlite'), patch.object(memory, 'add') as add:
            await g.execute_command(msg)
        add.assert_not_called()
        self.assertIn('approved', msg.reply.await_args.args[0])


class Memory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(memory, 'DB_PATH', Path(self.temp.name) / 'memory.db')
        self.patch.start()
        self.uid = 111111111111111111

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_same_second_new_note_conflicts_without_loss(self):
        with patch.object(memory.time, 'time', return_value=100):
            memory.add(self.uid, 'alice', 'likes tea')
            exported = memory.export_text()
            memory.add(self.uid, 'alice', 'has a dog called biscuit')
            with self.assertRaisesRegex(ValueError, 'changed'):
                memory.import_text(exported)
        self.assertEqual(len(memory.pick(self.uid, '', 25)), 2)

    def test_updated_note_conflicts_without_loss(self):
        memory.add(self.uid, 'alice', 'likes green tea')
        exported = memory.export_text()
        memory.add(self.uid, 'alice', 'likes black tea')
        with self.assertRaisesRegex(ValueError, 'changed'):
            memory.import_text(exported)
        self.assertIn('likes black tea', memory.pick(self.uid, '', 25))

    def test_current_export_edit_and_backup_restore(self):
        memory.add(self.uid, 'alice', 'likes tea')
        exported = memory.export_text().replace('likes tea', 'likes coffee')
        memory.import_text(exported)
        self.assertEqual(memory.pick(self.uid, '', 25), ['likes coffee'])
        backup = next(Path(self.temp.name).glob('*.bak-*'))
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('SELECT note FROM notes').fetchone()[0], 'likes tea')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o660)

    def test_initial_example_only_on_empty_database(self):
        example = (ROOT / 'latochan-memory.example.txt').read_text()
        memory.import_text(example)
        with self.assertRaisesRegex(ValueError, 'empty database'):
            memory.import_text(example)

    def test_oversized_import_is_rejected_before_change(self):
        memory.add(self.uid, 'alice', 'likes tea')
        exported = memory.export_text()
        data = exported + '\n'.join(f'fact number {i}' for i in range(30))
        with self.assertRaisesRegex(ValueError, 'maximum'):
            memory.import_text(data)
        self.assertEqual(memory.pick(self.uid, '', 25), ['likes tea'])

    def test_read_and_write_transactions_serialize(self):
        memory.add(self.uid, 'alice', 'likes tea')
        exported = memory.export_text()
        connection = sqlite3.connect(memory.DB_PATH)
        connection.execute('BEGIN IMMEDIATE')
        errors = []
        def importer():
            try:
                memory.import_text(exported)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=importer)
        worker.start()
        time.sleep(.05)
        connection.execute('INSERT INTO notes(user_id,name,note,created,updated) VALUES(?,?,?,?,?)',
                           (self.uid, 'alice', 'concurrent note', 100, 100))
        connection.commit()
        connection.close()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn('concurrent note', memory.pick(self.uid, '', 25))


class Files(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_gif_validation_does_not_partially_apply(self):
        with patch.dict(os.environ, GIF_DIR=str(self.directory)):
            with self.assertRaises(ValueError):
                gifstore.apply('replace', {'happy': ['https://example.com/gif'], 'sad': [f'https://example.com/{i}' for i in range(301)]})
            self.assertEqual(gifstore.links_of('happy'), [])

    def test_concurrent_gif_additions_survive(self):
        with patch.dict(os.environ, GIF_DIR=str(self.directory)):
            threads = [threading.Thread(target=gifstore.apply, args=('add', {'happy': [f'https://example.com/{i}']})) for i in range(10)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(len(gifstore.links_of('happy')), 10)

    def test_settings_retain_last_good_on_missing_or_corrupt_file(self):
        path = self.directory / 'settings.json'
        botsettings.write_patch({'server_powers': False, 'temperature': 1.0}, path)
        live = botsettings.Live(path, ttl=0)
        self.assertEqual(live('temperature'), 1.0)
        path.write_text('{')
        self.assertEqual(live('temperature'), 1.0)
        with self.assertRaises(ValueError): botsettings.write_patch({'server_powers': True}, path)
        path.unlink()
        self.assertEqual(live('temperature'), 1.0)
        self.assertFalse(live('server_powers'))

    def test_settings_read_does_not_wait_for_writer(self):
        from storage import locked
        path = self.directory / 'settings.json'
        botsettings.write_patch({'temperature': 1.0}, path)
        live = botsettings.Live(path, ttl=0)
        live.refresh()
        with locked(str(path) + '.lock'):
            started = time.monotonic()
            self.assertEqual(live('temperature'), 1.0)
            self.assertLess(time.monotonic() - started, .1)

    def test_existing_shared_lock_does_not_require_ownership(self):
        from storage import locked
        path = self.directory / 'shared.lock'
        path.touch(mode=0o660)
        with patch('storage.os.fchmod', side_effect=PermissionError('not inode owner')) as chmod:
            with locked(path):
                pass
            chmod.assert_not_called()

    def test_settings_failed_replace_preserves_previous_file(self):
        path = self.directory / 'settings.json'
        botsettings.write_patch({'temperature': 1.0}, path)
        before = path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): botsettings.write_patch({'temperature': .5}, path)
        self.assertEqual(path.read_bytes(), before)

    def test_environment_parser_does_not_execute_shell(self):
        path = self.directory / 'env'
        path.write_text('OWNER_ID="123"\nBOT_NAME="hello world"\nOLLAMA_URL="$(touch dangerous)"\n')
        values = envconfig.load_ids(path)
        self.assertEqual(values['OWNER_ID'], '123')
        self.assertEqual(values['OLLAMA_URL'], '$(touch dangerous)')
        self.assertEqual(values['BOT_NAME'], 'hello world')

    def test_action_claim_is_atomic(self):
        with patch.dict(os.environ, ACTION_DB=str(self.directory / 'actions.sqlite')):
            results = []
            threads = [threading.Thread(target=lambda: results.append(actionlog.claim(123, 'MUTE'))) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(sum(results), 1)


class Retrieval(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.patch = patch.multiple(rag, STORE_DIR=directory, DB=directory / 'store.sqlite')
        self.patch.start()
        self.path = directory / 'input.jsonl'
        self.path.write_text(json.dumps({'ts': '2026-10-29T10:00:00', 'author': 'me', 'text': 'likes tea', 'channel': 'private'}) + '\n')

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def vectors(self, texts):
        return rag.np.array([[1., 0.] for _ in texts], dtype=rag.np.float32)

    def test_embed_failure_commits_no_text_without_vector(self):
        with patch.object(rag, 'embed', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): rag.ingest([self.path])
        with sqlite3.connect(rag.DB) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0], 0)
        with patch.object(rag, 'embed', side_effect=self.vectors):
            rag.ingest([self.path])
            self.assertIn('likes tea', rag.Retriever().context_for('tea'))

    def test_repeated_ingest_is_idempotent(self):
        with patch.object(rag, 'embed', side_effect=self.vectors):
            rag.ingest([self.path]); rag.ingest([self.path])
        with sqlite3.connect(rag.DB) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0], 1)

    def test_channel_boundaries_and_timezone_normalization(self):
        self.path.write_text('\n'.join(json.dumps(m) for m in [
            {'ts': '2026-10-29T10:00:00Z', 'author': 'me', 'text': 'one', 'channel': 'a'},
            {'ts': '2026-10-29T10:01:00', 'author': 'me', 'text': 'two', 'channel': 'b'}]))
        self.assertEqual(len(list(rag.chunks_from(self.path))), 2)

    def test_legacy_files_are_not_modified(self):
        legacy = Path(self.temp.name) / 'rag_store.npz'
        legacy.write_bytes(b'legacy')
        with patch.object(rag, 'embed', side_effect=self.vectors): rag.ingest([self.path])
        self.assertEqual(legacy.read_bytes(), b'legacy')

    def test_model_change_is_rejected(self):
        with patch.object(rag, 'embed', side_effect=self.vectors): rag.ingest([self.path])
        with patch.object(rag, 'EMBED_MODEL', 'other'):
            with self.assertRaisesRegex(ValueError, 'model changed'): rag.Retriever()


class Availability(unittest.TestCase):
    def test_admission_is_bounded_and_released(self):
        admission = Admission(total=3, per_channel=3, reserved=0)
        self.assertTrue(admission.reserve((1, 1)))
        self.assertTrue(admission.reserve((1, 2)))
        self.assertFalse(admission.reserve((1, 3)))
        self.assertTrue(admission.reserve((2, 3)))
        self.assertFalse(admission.reserve((3, 4)))
        admission.release((1, 1)); admission.release((1, 1))
        self.assertTrue(admission.reserve((3, 4)))

    def test_expired_work_is_identified(self):
        admission = Admission(ttl=10)
        with patch('workload.time.monotonic', return_value=100): admission.reserve((1, 1))
        with patch('workload.time.monotonic', return_value=111): self.assertTrue(admission.expired((1, 1)))

    def test_prompt_budget_preserves_system_and_newest(self):
        settings = dict(botsettings.DEFAULTS)
        with patch.object(g, 'S', side_effect=lambda key: settings[key]), patch.object(g, 'NUM_CTX', 4096):
            messages = [{'role': 'system', 'content': 'system'}] + [
                {'role': role, 'content': 'x' * 300} for _ in range(20) for role in ('user', 'assistant')
            ] + [{'role': 'user', 'content': 'latest'}]
            result = g.budget_messages(messages)
            self.assertEqual(result[0], messages[0]); self.assertEqual(result[-1], messages[-1])
            self.assertLess(len(result), len(messages))
            self.assertLessEqual(sum(len(m['content'].encode()) + 32 for m in result), 4096 - 512 - 256)

    def test_oversized_prompt_is_rejected(self):
        with patch.object(g, 'NUM_CTX', 4096):
            with self.assertRaises(ValueError):
                g.budget_messages([{'content': 'system'}, {'content': 'x' * 5000}])


class Chat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = dict(botsettings.DEFAULTS, gifs_enabled=False)
        self.patch = patch.object(g, 'S', side_effect=lambda key: self.settings[key])
        self.patch.start()
        g.history.clear(); g.channel_locks.clear()

    async def asyncTearDown(self): self.patch.stop()

    async def test_public_owner_and_friend_cannot_load_private_context(self):
        for uid in (1, 200):
            with patch.object(memory, 'block') as block, patch.object(g, 'retriever') as retriever, patch.object(g, 'ask_llm', new=AsyncMock(return_value=('hi', ''))):
                await g.respond([message(uid=uid)], 'main')
                block.assert_not_called(); retriever.context_for.assert_not_called()

    async def test_model_cannot_write_memory_or_execute_actions(self):
        with patch.object(memory, 'add') as add, patch.object(g, 'do_actions', new=AsyncMock(return_value=[])) as actions, patch.object(g, 'ask_llm', new=AsyncMock(return_value=('hello [[REMEMBER: secret]] [[MUTE: 123]]', ''))):
            await g.respond([message()], 'main')
            add.assert_not_called()
            self.assertEqual(actions.await_args.args[1], [])

    async def test_model_failure_is_reported_to_requester(self):
        msg = message()
        with patch.object(g, 'ask_llm', new=AsyncMock(side_effect=RuntimeError('offline'))):
            await g.respond([msg], 'main')
        self.assertIn('could not answer', msg.reply.await_args.args[0])

    async def test_burst_limit_is_enforced(self):
        msg = message('a' * 200)
        key = (100, 1)
        burst = g.Burst('main')
        g.bursts[key] = burst
        try:
            for _ in range(30): await g.handle(msg)
            self.assertEqual(len(burst.msgs), 8)
        finally:
            g.bursts.clear()

    async def test_private_channel_rejects_other_authors(self):
        with patch.object(g, 'PRIVATE_CHANNELS', {100}), patch.object(g, 'respond', new=AsyncMock()) as respond:
            await g.handle(message(uid=200))
            respond.assert_not_awaited()


class Media(unittest.IsolatedAsyncioTestCase):
    async def test_real_decoder_process_accepts_small_png(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'red').save(buffer, 'PNG')
        frames = await g.decode_image(buffer.getvalue())
        self.assertEqual(len(frames), 1)

    async def test_decoder_rejects_unapproved_format(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4)).save(buffer, 'BMP')
        with self.assertRaises(ValueError): await g.decode_image(buffer.getvalue())

    async def test_decoder_does_not_scan_full_animation(self):
        from PIL import Image
        buffer = io.BytesIO()
        frames = [Image.new('RGB', (8, 8), (n, 0, 0)) for n in range(10)]
        frames[0].save(buffer, 'GIF', save_all=True, append_images=frames[1:])
        self.assertEqual(len(await g.decode_image(buffer.getvalue())), 3)

    async def test_external_and_redirect_hosts_not_admitted(self):
        for url in ['http://cdn.discordapp.com/a', 'https://127.0.0.1/a', 'https://cdn.discordapp.com.evil.test/a']:
            self.assertFalse(g.is_discord_media(url))
        self.assertTrue(g.is_discord_media('https://cdn.discordapp.com/a'))



class Inference(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = dict(botsettings.DEFAULTS)
        self.response = types.SimpleNamespace(raise_for_status=lambda: None,
                                              json=AsyncMock(return_value={'message': {'content': 'answer'}}))
        @contextlib.asynccontextmanager
        async def post(*args, **kwargs):
            self.payload = kwargs.get('json')
            yield self.response
        self.session = types.SimpleNamespace(post=post)
        self.patches = [patch.object(g, 'http', return_value=self.session),
                        patch.object(g, 'S', side_effect=lambda key: self.settings[key]),
                        patch.object(g, 'thinking_controls', []),
                        patch.object(g, 'inference_slots', asyncio.Semaphore(1))]
        for item in self.patches: item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches): item.stop()

    async def test_text_model_drops_images_and_disables_unsupported_thinking(self):
        with patch.object(g, 'model_capabilities', new=AsyncMock(return_value={'completion'})):
            result = await g.ask_llm([{'role': 'system', 'content': 'system'},
                                      {'role': 'user', 'content': 'hi', 'images': ['image']}], think=True)
        self.assertEqual(result[0], 'answer')
        self.assertFalse(self.payload['think'])
        self.assertNotIn('images', self.payload['messages'][-1])

    async def test_vision_model_gets_only_one_image(self):
        with patch.object(g, 'model_capabilities', new=AsyncMock(return_value={'vision', 'thinking'})):
            await g.ask_llm([{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hi', 'images': ['one', 'two']}], think=True)
        self.assertEqual(self.payload['messages'][-1]['images'], ['one'])
        self.assertTrue(self.payload['think'])

    async def test_named_thinking_level_is_supported(self):
        with patch.object(g, 'model_capabilities', new=AsyncMock(return_value={'thinking'})), patch.object(g, 'thinking_controls', ['low', 'high']):
            await g.ask_llm([{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hi'}], think=True)
        self.assertEqual(self.payload['think'], 'low')

    async def test_capability_cache_is_bound_to_model(self):
        self.response.json.return_value = {'capabilities': ['vision'], 'thinking': {'values': [False, True]}}
        with patch.object(g, '_capabilities_at', -1e9), patch.object(g, '_capabilities_model', None):
            self.assertEqual(await g.model_capabilities(), {'vision'})
            self.response.json.return_value = {'capabilities': ['completion']}
            with patch.object(g, 'MODEL', 'different-model'):
                self.assertEqual(await g.model_capabilities(), {'completion'})


class MoreRegressions(unittest.TestCase):
    def test_owner_capacity_is_reserved(self):
        admission = Admission(total=3, per_channel=2, reserved=1)
        self.assertTrue(admission.reserve((100, 200)))
        self.assertTrue(admission.reserve((101, 201)))
        self.assertFalse(admission.reserve((102, 202)))
        self.assertTrue(admission.reserve((100, 1), owner=True))

    def test_gif_storage_error_reports_partial_update(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, GIF_DIR=directory):
            original = gifstore._write
            def writer(mood, links):
                if mood == 'sad': raise OSError('disk full')
                original(mood, links)
            with patch.object(gifstore, '_write', side_effect=writer):
                with self.assertRaisesRegex(gifstore.PartialUpdateError, 'Earlier changes remain'):
                    gifstore.apply('replace', {'happy': ['https://example.com/one'], 'sad': ['https://example.com/two']})
            self.assertEqual(gifstore.links_of('happy'), ['https://example.com/one'])



class SayHelper(unittest.TestCase):
    def test_stdin_message_never_becomes_a_process_argument(self):
        import runpy
        content = '"; reboot; $(echo unsafe)'
        response = io.BytesIO(b'{"id":"123"}')
        with patch.object(sys, 'argv', ['lato-say', '--stdin', '-c', '100']), patch.object(sys, 'stdin', io.StringIO(content)), \
                patch.object(envconfig, 'load_ids', return_value={'DISCORD_BOT_TOKEN': 'dummy'}), \
                patch('urllib.request.urlopen', return_value=response) as send:
            runpy.run_path(str(ROOT / 'lato-say'), run_name='__main__')
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)['content'], content)
        self.assertNotIn(content, sys.argv)


if __name__ == '__main__':
    unittest.main()
