"""Console script for casket_assembler."""
import argparse
import sys
from   .ca import CasketAssembler

def main():
    ca = CasketAssembler()
    ca.process()
    pass


if __name__ == '__main__':
    res = main()
    sys.exit(0) # pragma: no cover

