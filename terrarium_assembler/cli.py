"""Console script for terrarium_assembler."""
import argparse
import sys
from   .ta import TerrariumAssembler

def main():
    print(f'Running TA from {__file__}')
    ta = TerrariumAssembler()
    ta.process()
    pass


if __name__ == '__main__':
    res = main()
    sys.exit(0) # pragma: no cover

