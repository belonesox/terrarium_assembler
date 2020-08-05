"""Console script for casket_assembler."""
import argparse
import sys
from   .ta import TerrariumAssembler

def main():
    ta = Terrarium()
    ta.process()
    pass


if __name__ == '__main__':
    res = main()
    sys.exit(0) # pragma: no cover

