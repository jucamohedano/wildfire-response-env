import json
import threading
import unittest

from eval.run_benchmark_subagent import Bridge, SubagentPolicy, MODEL


class SubagentBridgeTests(unittest.TestCase):
    def test_public_request_reply_roundtrip_and_stale_reply_rejection(self):
        bridge = Bridge(seconds=5)
        output = []
        payload = dict(messages=[dict(role='user', content='public observation')], tools=[])
        worker = threading.Thread(target=lambda: output.append(bridge(payload)))
        worker.start()
        request = bridge.next()
        self.assertEqual(set(request), {'request_id', 'payload'})
        request['payload']['messages'][0]['content'] = 'mutation'
        self.assertEqual(bridge.next()['payload']['messages'][0]['content'], 'public observation')
        with self.assertRaises(ValueError):
            bridge.submit(dict(request_id=0, name='advance_time', arguments={}))
        bridge.submit(dict(request_id=request['request_id'], name='advance_time', arguments={}))
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output[0]['model'], MODEL)
        call = output[0]['choices'][0]['message']['tool_calls'][0]
        self.assertEqual(json.loads(call['function']['arguments']), {})
        with self.assertRaises(ValueError):
            bridge.submit(dict(request_id=request['request_id'], name='advance_time', arguments={}))
        bridge.close()
        self.assertEqual(bridge.next(), dict(finished=True))

    def test_no_tactical_or_schema_repair_and_bounded_wait(self):
        bridge = Bridge(seconds=5)
        output = []
        worker = threading.Thread(target=lambda: output.append(bridge(dict(messages=[], tools=[]))))
        worker.start()
        request = bridge.next()
        bridge.submit(dict(request_id=request['request_id'], name='unknown', arguments={'bad': 1}))
        worker.join(timeout=3)
        call = output[0]['choices'][0]['message']['tool_calls'][0]['function']
        self.assertEqual(call['name'], 'unknown')
        self.assertEqual(json.loads(call['arguments']), {'bad': 1})
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            Bridge(seconds=0)({})

    def test_model_guard_preserves_default_free_api_policy(self):
        from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkModelPolicy
        with self.assertRaises(ValueError):
            SubagentPolicy(model='another-model', transport=lambda value: {})
        with self.assertRaises(ValueError):
            BenchmarkModelPolicy(model=MODEL, transport=lambda value: {})
        self.assertEqual(SubagentPolicy(model=MODEL, transport=lambda value: {}).contract_version,
                         'wildfire-benchmark-model-tools-v1')


if __name__ == '__main__':
    unittest.main()
