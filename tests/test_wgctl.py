import base64
import builtins
import json
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('wgctl', str(Path(__file__).resolve().parents[1] / 'wgctl'))
spec = importlib.util.spec_from_loader(loader.name, loader)
w = importlib.util.module_from_spec(spec)
loader.exec_module(w)


def key(number):
    return base64.b64encode(bytes([number]) * 32).decode()


def peer(number=1, ip='10.8.0.2/32', extra=''):
    return f'[Peer]\nPublicKey = {key(number)}\nAllowedIPs = {ip}\n{extra}'


def config(body='', address='10.8.0.1/24'):
    return w.Config(f'[Interface]\nAddress = {address}\nListenPort = 51820\nPrivateKey = {key(9)}\n' + body)


def live(number=1, ip='10.8.0.2/32'):
    return {key(number): dict(fields={'publickey': key(number), 'allowedips': ip}, handshake=0, rx=10, tx=20)}


class Discovery(unittest.TestCase):
    def test_plain_legacy_disabled_and_named_peers(self):
        text = peer(1) + '# BEGIN_PEER phone added=2026-01-01\n' + peer(2, '10.8.0.3/32') + '# END_PEER phone\n'
        text += '# BEGIN_PEER tablet\n' + ''.join('#~ ' + line for line in peer(3, '10.8.0.4/32').splitlines(True)) + '# END_PEER tablet\n'
        text += peer(4, '10.8.0.5/32', '# Name = desktop\n')
        parsed = config(text)
        self.assertEqual(len(parsed.peers), 4)
        self.assertEqual(parsed.peers[key(1)]['name'], '')
        self.assertEqual(parsed.peers[key(2)]['name'], 'phone')
        self.assertTrue(parsed.peers[key(3)]['disabled'])
        self.assertEqual(parsed.peers[key(4)]['name'], 'desktop')

    def test_comments_and_multiple_allowedips(self):
        parsed = config(f'[Peer] # device\n PublicKey = {key(1)} # public\nAllowedIPs = 10.8.0.2/32\nAllowedIPs = fd00::2/128 # v6\n')
        self.assertEqual(len(w.allowed(parsed.peers[key(1)]['fields'])), 2)

    def test_duplicate_or_missing_key_rejected(self):
        for body in (peer() + peer(), '[Peer]\nAllowedIPs = 10.8.0.2/32\n'):
            with self.assertRaises(w.Error):
                config(body)

    def test_listing_union_and_honest_states(self):
        parsed = config(peer(1) + peer(2, '10.8.0.3/32'))
        current = live(1)
        current.update(live(3, '10.8.0.4/32'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.listing('wg0', parsed, current, True)
        result = out.getvalue()
        self.assertEqual(result.count('unknown device'), 3)
        for label in ('active', 'not_saved', 'not_loaded'):
            self.assertIn(label, result)
        self.assertNotIn('disconnected', result)
        self.assertNotIn(key(9), result)

    def test_disabled_but_live_is_visible(self):
        parsed = config(peer())
        parsed = w.Config(parsed.edit(key(1), 'off'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.listing('wg0', parsed, live(), True)
        self.assertIn('disabled_but_active', out.getvalue())

    def test_ip_mismatch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w.listing('wg0', config(peer()), live(ip='10.8.0.9/32'), True)
        self.assertIn('ip_mismatch', out.getvalue())

    def test_selectors_and_ambiguity(self):
        parsed = config(peer(1, extra='# Name = phone\n'))
        for selector in ('phone', '10.8.0.2', key(1)[:12], key(1)):
            self.assertEqual(w.resolve(selector, parsed, {}), key(1))
        with self.assertRaises(w.Error):
            w.resolve('unknown device', parsed, {})
        with self.assertRaises(w.Error):
            w.resolve('10.8.0.2', parsed, live(2))


class Editing(unittest.TestCase):
    def test_toggle_plain_peer_preserves_others_and_roundtrips(self):
        parsed = config(peer(1) + '# BEGIN_PEER second\n' + peer(2) + '# END_PEER second\n')
        disabled = w.Config(parsed.edit(key(1), 'off'))
        self.assertTrue(disabled.peers[key(1)]['disabled'])
        self.assertFalse(disabled.peers[key(2)]['disabled'])
        self.assertEqual(disabled.edit(key(1), 'on'), parsed.text)
        self.assertEqual(disabled.edit(key(1), 'off'), disabled.text)

    def test_remove_preserves_adjacent_legacy_block(self):
        other = '# BEGIN_PEER second\n' + peer(2) + '# END_PEER second\n'
        parsed = config(peer(1) + other)
        result = parsed.edit(key(1), 'rm')
        self.assertTrue(result.endswith(other))
        self.assertNotIn(key(1), result)
        self.assertEqual(len(w.Config(result).peers), 1)

    def test_rename_disabled_legacy_device(self):
        parsed = config('# BEGIN_PEER old\n' + peer() + '# END_PEER old\n' + peer(2))
        parsed = w.Config(parsed.edit(key(1), 'off'))
        renamed = w.Config(parsed.edit(key(1), 'rename', 'phone'))
        self.assertEqual(renamed.peers[key(1)]['name'], 'phone')
        self.assertTrue(renamed.peers[key(1)]['disabled'])
        self.assertIn(key(2), renamed.peers)
        self.assertNotIn('BEGIN_PEER old', renamed.text)

    def test_missing_final_newline_on_last_value(self):
        parsed = w.Config(config(peer()).text.rstrip())
        renamed = w.Config(parsed.edit(key(1), 'rename', 'phone'))
        self.assertEqual(renamed.peers[key(1)]['fields']['allowedips'], '10.8.0.2/32')


class Allocation(unittest.TestCase):
    def test_actual_subnet_and_network_address(self):
        self.assertEqual(w.allocate(config(address='10.8.0.129/25'), {}), ('10.8.0.130', '10.8.0.129'))

    def test_disabled_and_runtime_addresses_reserved(self):
        parsed = config(peer())
        parsed = w.Config(parsed.edit(key(1), 'off'))
        self.assertEqual(w.allocate(parsed, live(2, '10.8.0.3/32'))[0], '10.8.0.4')

    def test_routed_prefixes_reserved(self):
        parsed = config(peer(1, '10.0.0.0/9'), address='10.0.0.1/8')
        self.assertEqual(w.allocate(parsed, {})[0], '10.128.0.0')

    def test_exhaustion(self):
        with self.assertRaises(w.Error):
            w.allocate(config(peer(1), address='10.8.0.1/30'), {})

    def test_multiple_addresses_and_ipv6(self):
        parsed = config(address='fd00::1/64, 10.8.0.1/30, 10.8.0.2/30')
        with self.assertRaises(w.Error):
            w.allocate(parsed, {})
        with self.assertRaises(w.Error):
            w.allocate(config(address='fd00::1/64'), {})

    def test_point_to_point_subnet(self):
        self.assertEqual(w.allocate(config(address='10.8.0.0/31'), {})[0], '10.8.0.1')


class Changes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'wg0.conf'
        self.config = config(peer(1) + peer(2, '10.8.0.3/32'))
        self.path.write_text(self.config.text)

    def test_off_updates_only_selected_peer_and_private_backup(self):
        updated = self.config.edit(key(1), 'off')
        with patch.object(w, 'apply_peer') as apply:
            w.commit(self.path, self.config, updated, 'wg0', key(1), None, live(), True)
        apply.assert_called_once_with('wg0', key(1), None)
        self.assertEqual(self.path.read_text(), updated)
        backup = self.path.with_suffix('.conf.bak')
        self.assertEqual(backup.read_text(), self.config.text)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_update_restores_file_and_peer(self):
        with patch.object(w, 'apply_peer', side_effect=[w.Error('failed'), None, None]) as apply:
            with self.assertRaisesRegex(w.Error, 'previous configuration was restored'):
                w.commit(self.path, self.config, self.config.edit(key(1), 'off'), 'wg0', key(1), None, live(), True)
        self.assertEqual(self.path.read_text(), self.config.text)
        self.assertEqual(apply.call_args_list[-1].args, ('wg0', key(1), live()[key(1)]['fields']))

    def test_failed_rollback_is_reported(self):
        with patch.object(w, 'apply_peer', side_effect=w.Error('failed')):
            with self.assertRaisesRegex(w.Error, 'live state could not be restored'):
                w.commit(self.path, self.config, self.config.edit(key(1), 'off'), 'wg0', key(1), None, live(), True)
        self.assertEqual(self.path.read_text(), self.config.text)

    def test_os_error_during_live_update_also_restores_file_and_peer(self):
        with patch.object(w, 'apply_peer', side_effect=[OSError('temporary file failed'), None, None]) as apply:
            with self.assertRaisesRegex(w.Error, 'previous configuration was restored'):
                w.commit(self.path, self.config, self.config.edit(key(1), 'off'),
                         'wg0', key(1), None, live(), True)
        self.assertEqual(self.path.read_text(), self.config.text)
        self.assertEqual(apply.call_args_list[-1].args,
                         ('wg0', key(1), live()[key(1)]['fields']))

    def test_failed_file_restore_still_attempts_live_restore(self):
        with patch.object(w, 'atomic_write', side_effect=OSError('disk failed')), \
             patch.object(w, 'apply_peer') as apply:
            with self.assertRaisesRegex(w.Error, 'saved configuration could not be restored'):
                w.restore_change(self.path, self.config, 'wg0', key(1), live())
        self.assertEqual(apply.call_args_list[-1].args,
                         ('wg0', key(1), live()[key(1)]['fields']))

    def test_saveconfig_blocks_edits(self):
        parsed = w.Config(self.config.text.replace('[Interface]', '[Interface]\nSaveConfig = true'))
        self.path.write_text(parsed.text)
        with patch.object(w, 'apply_peer') as apply, self.assertRaisesRegex(w.Error, 'SaveConfig'):
            w.commit(self.path, parsed, parsed.edit(key(1), 'off'), 'wg0', key(1), None, live(), True)
        apply.assert_not_called()
        self.assertEqual(self.path.read_text(), parsed.text)

    def test_external_edit_not_overwritten(self):
        self.path.write_text(self.config.text + '# external change\n')
        with self.assertRaisesRegex(w.Error, 'changed while'):
            w.commit(self.path, self.config, self.config.edit(key(1), 'off'), 'wg0', key(1), None, live(), True)

    def test_down_interface_saves_without_live_update(self):
        with patch.object(w, 'apply_peer') as apply, contextlib.redirect_stderr(io.StringIO()):
            w.commit(self.path, self.config, self.config.edit(key(1), 'off'), 'wg0', key(1), None, {}, False)
        apply.assert_not_called()
        self.assertTrue(w.Config(self.path.read_text()).peers[key(1)]['disabled'])

    def test_rename_does_not_touch_runtime(self):
        with patch.object(w, 'apply_peer') as apply:
            w.commit(self.path, self.config, self.config.edit(key(1), 'rename', 'phone'), 'wg0', key(1), None, live(), True, False)
        apply.assert_not_called()


class Profiles(unittest.TestCase):
    def test_explicit_endpoints(self):
        for host, expected in [('vpn.example.com', 'vpn.example.com:51820'),
                               ('vpn.example.com:443', 'vpn.example.com:443'),
                               ('2001:db8::1', '[2001:db8::1]:51820'),
                               ('[2001:db8::1]:443', '[2001:db8::1]:443')]:
            self.assertEqual(w.endpoint(config(), {'ENDPOINT': host}), expected)

    def test_private_endpoint_requires_one_setting(self):
        with patch.object(w, 'run', return_value='[{"prefsrc":"192.168.1.2"}]'):
            with self.assertRaisesRegex(w.Error, 'ENDPOINT='):
                w.endpoint(config(), {})

    def test_explicit_endpoints_cannot_skip_validation(self):
        for host in ('vpn.example.com:0', 'vpn.example.com:65536', 'vpn.example.com:abc',
                     'bad host:443', 'vpn.example.com#comment:443', ':443',
                     '[192.0.2.1]:443', '[2001:db8::1]:0'):
            with self.subTest(host=host), self.assertRaises((w.Error, ValueError)):
                w.endpoint(config(), {'ENDPOINT': host})

    def test_explicit_port_works_without_server_listenport(self):
        parsed = w.Config('[Interface]\n')
        self.assertEqual(w.endpoint(parsed, {'ENDPOINT': 'vpn.example.com:443'}),
                         'vpn.example.com:443')
        self.assertEqual(w.endpoint(config(), {'ENDPOINT': '[2001:db8::1]'}),
                         '[2001:db8::1]:51820')

    def test_profile_and_server_peer_agree(self):
        args = w.parser().parse_args(['add', 'phone'])
        args.interface = 'wg0'
        output = io.StringIO()
        generated = [key(3), key(4), key(5), key(6)]
        with patch.object(w, 'run', side_effect=generated), patch.object(w, 'private_stdout'), \
             patch.object(w, 'commit') as commit, contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(io.StringIO()):
            w.add(args, Path('/unused'), config(), {}, True, {'ENDPOINT': 'vpn.example.com'})
        profile = output.getvalue()
        self.assertIn('Address = 10.8.0.2/32', profile)
        self.assertIn('AllowedIPs = 10.8.0.1/32', profile)
        self.assertNotIn('DNS =', profile)
        saved = w.Config(commit.call_args.args[2])
        self.assertEqual(saved.peers[key(4)]['fields']['presharedkey'], key(5))
        self.assertNotIn(key(3), saved.text)


class Commands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / 'wireguard'
        self.server.mkdir()
        self.path = self.server / 'wg0.conf'
        self.path.write_text(config(peer()).text)
        self.real_open = builtins.open

    def mapped_path(self, value):
        if str(value) == '/etc/wireguard':
            return self.server
        if str(value) == '/etc/wgctl.conf':
            return self.root / 'settings'
        return Path(value)

    def mapped_open(self, value, *args, **kwargs):
        if str(value) == '/run/wgctl.lock':
            value = self.root / 'lock'
        return self.real_open(value, *args, **kwargs)

    def execute(self, argv, active='wg0'):
        self.calls = []

        def command(*args, **kwargs):
            self.calls.append(args)
            if args == ('wg', 'show', 'interfaces'):
                return active
            if args[:2] == ('wg', 'show') and args[-1] == 'dump':
                return key(9) + '\t' + key(8) + '\t51820\t0\n' + '\t'.join(
                    [key(1), '(none)', '(none)', '10.8.0.2/32', '0', '10', '20', '0'])
            if args[:2] == ('wg', 'set'):
                return ''
            raise AssertionError(args)

        self.output = io.StringIO()
        with patch.object(w, 'Path', side_effect=self.mapped_path), \
             patch.object(w.os, 'geteuid', return_value=0), patch.object(w.os, 'umask'), \
             patch('builtins.open', side_effect=self.mapped_open), patch.object(w, 'run', side_effect=command), \
             contextlib.redirect_stdout(self.output):
            w.main(argv)
        return self.output.getvalue()

    def test_default_command_lists_plain_device(self):
        result = self.execute([])
        self.assertIn('unknown device', result)
        self.assertIn('active', result)

    def test_ls_does_not_require_address_port_or_saveconfig_false(self):
        self.path.write_text('[Interface]\nSaveConfig = true\n' + peer())
        self.assertIn('unknown device', self.execute(['show']))

    def test_runtime_only_interface_is_listed(self):
        self.path.unlink()
        self.assertIn('not_saved', self.execute(['show']))

    def test_records_across_interfaces_have_stable_fields_and_numeric_counters(self):
        (self.server / 'wg1.conf').write_text(config(peer(2)).text)
        result = self.execute(['-j', 'show'])
        rows = [json.loads(line) for line in result.splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['interface'] for row in rows}, {'wg0', 'wg1'})
        expected = {'interface', 'name', 'ip', 'state', 'handshake',
                    'received_bytes', 'sent_bytes', 'public_key'}
        self.assertTrue(all(set(row) == expected for row in rows))
        runtime = next(row for row in rows if row['interface'] == 'wg0')
        self.assertEqual(runtime['name'], 'unknown device')
        self.assertEqual(runtime['public_key'], key(1))
        self.assertEqual(runtime['ip'], '10.8.0.2/32')
        self.assertEqual(runtime['handshake'], 0)
        self.assertEqual(runtime['received_bytes'], 10)
        self.assertEqual(runtime['sent_bytes'], 20)
        saved = next(row for row in rows if row['interface'] == 'wg1')
        self.assertEqual(saved['state'], 'interface_down')
        self.assertIsNone(saved['received_bytes'])

    def test_empty_listing_has_no_records(self):
        self.path.write_text(config().text)
        self.assertEqual(self.execute(['-j', 'show'], active=''), '')

    def test_record_values_with_spaces_quotes_and_equals_roundtrip(self):
        self.path.write_text(config(peer(extra="# Name = Sam's phone = work\n")).text)
        row = json.loads(self.execute(['-j', 'show']))
        self.assertEqual(row['name'], "Sam's phone = work")
        self.assertEqual(row['public_key'], key(1))

    def test_docker_style_table_has_one_header_and_one_row_per_device(self):
        (self.server / 'wg1.conf').write_text(config(peer(2)).text)
        lines = self.execute(['show']).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(sum(line.startswith('INTERFACE') for line in lines), 1)
        self.assertTrue(lines[1].startswith('wg0 '))
        self.assertTrue(lines[2].startswith('wg1 '))
        self.assertIn(key(1)[:12], lines[1])
        self.assertIn('10B', lines[1])
        self.assertIn('never', lines[1])

    def test_empty_table_has_only_header(self):
        self.path.write_text(config().text)
        lines = self.execute(['show'], active='').splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('INTERFACE'))

    def test_off_by_ip_edits_plain_peer_and_targets_only_it(self):
        self.execute(['se', '10.8.0.2', 'd', 'd', 'wg0'])
        self.assertTrue(w.Config(self.path.read_text()).peers[key(1)]['disabled'])
        mutations = [call for call in self.calls if call[1] == 'set']
        self.assertEqual(mutations, [('wg', 'set', 'wg0', 'peer', key(1), 'remove')])

    def test_multiple_interfaces_need_selection_for_changes(self):
        (self.server / 'wg1.conf').write_text(config(peer(2)).text)
        with self.assertRaisesRegex(w.Error, 'Choose an interface'):
            self.execute(['set', '10.8.0.2', 'down'])
        self.execute(['set', '10.8.0.2', 'down', 'dev', 'wg0'])
        self.assertFalse(w.Config((self.server / 'wg1.conf').read_text()).peers[key(2)]['disabled'])

    def test_rename_by_id_keeps_runtime_untouched(self):
        self.execute(['se', key(1)[:12], 'n', 'phone', 'd', 'wg0'])
        self.assertEqual(w.Config(self.path.read_text()).peers[key(1)]['name'], 'phone')
        self.assertFalse(any(call[1] == 'set' for call in self.calls))

    def test_enable_conflict_leaves_file_unchanged(self):
        original = config(peer(1) + peer(2)).text
        self.path.write_text(original)
        with self.assertRaisesRegex(w.Error, 'same AllowedIPs prefix'):
            self.execute(['set', key(2), 'up'])
        self.assertEqual(self.path.read_text(), original)
        self.assertFalse(any(call[1] == 'set' for call in self.calls))

    def test_backup_does_not_require_valid_peer_config_or_live_dump(self):
        self.path.write_text('[Interface]\n[Peer]\nPublicKey = broken\n')
        with patch.object(w, 'backup') as archive:
            self.execute(['backup'])
        archive.assert_called_once_with(self.path)
        self.assertEqual(self.calls, [('wg', 'show', 'interfaces')])


class Arguments(unittest.TestCase):
    def test_command_prefixes(self):
        for word in ('s', 'sh', 'sho', 'show', 'l', 'li', 'lis', 'list'):
            self.assertEqual(w.parse_args([word]).command, 'show')
        for word in ('a', 'ad', 'add'):
            self.assertEqual(w.parse_args([word, 'phone']).command, 'add')
        for word in ('d', 'de', 'del', 'dele', 'delet', 'delete'):
            args = w.parse_args([word, 'phone'])
            self.assertEqual((args.command, args.action), ('delete', 'rm'))
        for word in ('b', 'ba', 'bac', 'back', 'backu', 'backup'):
            self.assertEqual(w.parse_args([word]).command, 'backup')

    def test_set_operation_and_dev_prefixes(self):
        for verb in ('se', 'set'):
            for operation in ('d', 'do', 'dow', 'down'):
                for dev in ('d', 'de', 'dev'):
                    args = w.parse_args([verb, 'phone', operation, dev, 'wg0'])
                    self.assertEqual((args.command, args.action, args.device, args.interface),
                                     ('set', 'off', 'phone', 'wg0'))
            for operation in ('u', 'up'):
                self.assertEqual(w.parse_args([verb, 'phone', operation]).action, 'on')
            for operation in ('n', 'na', 'nam', 'name'):
                args = w.parse_args([verb, 'phone', operation, 'tablet', 'd', 'wg0'])
                self.assertEqual((args.action, args.name, args.interface), ('rename', 'tablet', 'wg0'))

    def test_device_names_are_never_expanded(self):
        for name in ('s', 'se', 'd', 'dev', 'down', 'n', 'name'):
            args = w.parse_args(['se', name, 'n', name, 'd', 'wg0'])
            self.assertEqual((args.device, args.name), (name, name))
            args = w.parse_args(['a', name, 'd', 'wg0', '--full'])
            self.assertEqual(args.name, name)
            self.assertTrue(args.full)
        args = w.parse_args(['a', '--full', 'dev', 'd', 'wg0'])
        self.assertEqual((args.name, args.interface), ('dev', 'wg0'))

    def test_json_flags_before_and_after_show(self):
        for flag in ('-j', '-json', '--json'):
            for argv in ([flag, 'sh', 'd', 'wg0'], ['sh', 'd', 'wg0', flag], [flag]):
                args = w.parse_args(argv)
                self.assertEqual((args.command, args.output), ('show', 'json'))

    def test_incomplete_or_invalid_syntax_is_rejected(self):
        for argv in (['sh', 'd'], ['se', 'phone'], ['se', 'phone', 'n'],
                     ['se', 'phone', 'd', 'd'], ['-j', 'del', 'phone'],
                     ['a', 'phone', 'bogus', 'wg0'], ['sh', 'dev', 'wg0', 'extra']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    w.parse_args(argv)
                self.assertEqual(exc.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
