import os
from minc2_simple import minc2_file
from pyminc.volumes.factory import *
from numpy import *
import sys


if __name__ == "__main__":
    in_f = 'data/lng_subject43_3_t1_grid_0.mnc'
    ot_f = 'data/lng_subject43_3_t1_grid_x.mnc'

    # get the input file
    infile = volumeFromFile(in_f)
    # get the output file using the same dimension info as the input file
    outfile = volumeLikeFile(in_f, ot_f)

    # add one to the data 
    outfile.data = infile.data + 1

    # write out and close the volumes
    outfile.writeFile()
    outfile.closeVolume()
    infile.closeVolume()