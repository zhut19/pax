#!/usr/bin/python

import json
import gzip

import numpy as np

from tqdm import tqdm
import time


description = \
"""
Map of the Xenon100 x,y light collection efficiency in the S2 electroluminescence region.
Correction map type: Vector2DGridMap

The map 'map' is 98-vector valued (1 for each top PMT in Xenon100), describing the
relative light collection efficiencies of the PMT, i.e. the probability of an isotropically
produced photon detected by one of our PMTs (in the top array?) to arrive at this particular PMT.
Also contains a map named 'total_LCE' describing the probability of an isotropically 
produced photon to arrive at ANY PMT in the top array.

The first index to the maps should be the x axis, the second the y (unlike interpolate2d from scipy).

Generated by Yuehan Mei in ~2011 (?) by simulating isotropic photon production in GEANT4
Photons were produced on points separated by 2.5 mm. 

Converted to pax format by Bart Pelssers and Jelle Aalbers, November 2014.
Assumptions made:
    -   the simulated photons were produced precisely at each gridpoint.
    -   When the map has no data for a point, photons will never be detected from there.
        (the map's datapoints were not on a regular grid, some datapoints around the edges
        were missing)       
    -   Any bin further than 16cm out from center is zeroed.
"""

def convert_lce_map(filename, outname, n_channels=98, tpc_radius=15.5):
    """Convert the Xerawdp simulated S2 lce per pmt map to a pax map
    Please allow some fudge factor in tpc radius; bin edge vs bin center stuff..
    """
    
    # Slurp the file
    print("Slurping old map")
    with open(filename,"r") as input_file:
        raw_map = input_file.read().splitlines()
   
    """
    Read the header.
    Line 3 of the old map gives the geometry of the map (dimensions are scaled by factor 2)
      nx     number of x gridpoints
      x_min  first x value
      dx     grid x spacing
      n_y    number of y gridpoints
      y_min  first y value
      d_y    grid y spacing
    
    """
    to_cm = 0.05 # To convert from 1 = 0.5 mm (units of the old map) to 1 = 1 cm (units of pax)
    n_x, x_min, d_x, n_y, y_min, d_y = map(int, raw_map[3].split())
    n_pmts = 98
    
    # Note x_min, y_min are negative...
    assert n_x == -2*x_min/d_x+1
    assert n_y == -2*y_min/d_y+1

    lcemap =  np.zeros((n_x, n_y, n_pmts + 1))
    output = {}
    output['total_LCE'] = np.zeros((n_x, n_y)).tolist()
    output['name'] = outname
    output['coordinate_system'] = [
        ["x", [x_min*to_cm, -x_min*to_cm, n_x]],
        ["y", [y_min*to_cm, -y_min*to_cm, n_y]]
    ]
    output['description'] = description
    output['timestamp'] = time.time()


    print("Converting values...")
    for line_index, line in enumerate(tqdm(raw_map[4:])):
        # lines are composed of: x, y, Nhits/Nphotons, {pmt1, pmt98}
        # pmt1 = hitsonpmt1 / Nhits
        # linedata[2] should be n_hit_any_pmt/n_produced (i.e. the overall lce at this position)
        linedata = list(map(float, line.split()))
        assert len(linedata) == n_channels + 3
        if len(linedata) <= 1:
            continue

        # Compute the x and y indexes
        x = linedata[0]
        y = linedata[1]
        x_index = int((x - x_min)/d_x)
        y_index = int((y - y_min)/d_y)
        overall_lce = linedata[2]
        
        # Several datapoints are missing (near the edges??), so the line_index is not useful.
        # We can't do this:
        # assert x_index == math.floor(line_index / n_x)
        # assert y_index == int(line_index % n_x)
        if overall_lce > 0:
            assert abs(1-sum(linedata[3:])) < 0.001
        lcemap[y_index, x_index, 0] = 0     # PMT 0 is fake...
        for pmt_i, fraction_of_light_received in enumerate(linedata[3:]):
            # Ensure no light comes from invalid regions.
            # Remember x and y are in mm, tpc_radius is in cm...
            if overall_lce == 0 or (x * to_cm)**2 + (y * to_cm)**2 > tpc_radius**2:
                result = 0
            else:
                result = fraction_of_light_received #* overall_lce
            lcemap[x_index, y_index, pmt_i + 1] = result
        output['total_LCE'][x_index][y_index] = overall_lce

    output['map'] = np.array(lcemap).tolist()
     
    print("Dumping to json: this may take a while...")
        
    # Show some plots for sanity check
    # import matplotlib.pyplot as plt
    # plt.pcolor(np.array(output[1])) 
    # plt.colorbar()
    # plt.title('LCE for PMT 1')
    # plt.show()
    # plt.scatter(xs,ys)
    # plt.title('x,y datapoints present in the map')
    # plt.show()
    
    with gzip.open(outname, 'wb') as outfile:
        #pickle.dump(output, outfile)
        #outfile.write(bson.BSON.encode(output))
        bla = json.dumps(output)
        outfile.write(bla.encode())

convert_lce_map("xy-lce-map2.5mm.dat", "XENON100_s2_xy_patterns_Xerawdp0.4.5.json.gz")