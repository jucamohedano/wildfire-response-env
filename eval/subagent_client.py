"""Public actor client. Contains no simulator access or policy logic."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--reply')
    parser.add_argument('--after', type=int, default=0)
    args = parser.parse_args()
    url = json.loads(Path(args.connection).read_text())['url']
    if args.reply:
        body = json.loads(args.reply)
        request = Request(url+'/reply', data=json.dumps(body, allow_nan=False).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=60) as response:
            json.load(response)
        args.after = body['request_id']
    with urlopen(url+f'/next?after={args.after}', timeout=65) as response:
        result = json.load(response)
    if not args.full and 'payload' in result:
        payload = result.pop('payload')
        result['messages'] = payload['messages'][1:]
        result['available_tools'] = [tool['function']['name'] for tool in payload['tools']]
    print(json.dumps(result, separators=(',', ':'), ensure_ascii=False))


if __name__ == '__main__':
    main()
