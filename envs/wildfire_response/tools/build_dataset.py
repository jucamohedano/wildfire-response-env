"""Export the frozen task pack; generated private configurations remain evaluator-only."""
import argparse

from envs.wildfire_response.regional.evaluate import export_tasks


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    export_tasks(args.output)


if __name__ == '__main__':
    main()
