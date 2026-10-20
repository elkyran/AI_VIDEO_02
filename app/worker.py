"""Internal worker entry point; callers enqueue jobs through the validated API."""
import argparse
from .jobs import JobManager

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--job',required=True)
    args=parser.parse_args()
    JobManager(args.workspace).work(args.job)

if __name__=='__main__': main()
