#!/usr/bin/python

from __future__ import print_function

import sys
import re
import os
import time

S_IDLE = 0
S_PRIME_BLOCK = 1
S_START_EXTRUDER = 2
S_END_EXTRUDER = 3

raw_re_state_table = [
    (';TYPE:WALL.*', S_IDLE),
    (';TYPE:PRIME-TOWER', S_PRIME_BLOCK),
    ('; EXTRUDER START HOME', S_START_EXTRUDER),
    ('; EXTRUDER END HOME', S_END_EXTRUDER)
]

compiled_re_state_table = [(re.compile(k), v) for k, v in  raw_re_state_table]

tool_regex = re.compile('^T(?P<tool>[0-9]*)$')
temp_regex = re.compile('^M10[49]( T(?P<tool>[0-9]*))? S(?P<temp>[0-9]*)$')
move_z_regex = re.compile('^G[01] [^Z]*Z(?P<z_coordinate>[0-9](.[0-9]*)?).*$')
# move_regex = re.compile('^(?P<gcode>G[01]) ((?P<feed>F[0-9]*)|(?P<coordinate>[XYZ][0-9].?[0-9]*)')

move_regex = re.compile('^G[01]$')
feed_regex = re.compile('^F([0-9].)?[0-9]*$')
z_regex = re.compile('^Z([0-9].)?[0-9]*$')

def rewrite_move(move_string, feedrate, z_override, delete_prefixes=['Z', 'E']):
    components = move_string.split(' ')
    if not move_regex.match(components[0]):
        # not a move command, skip it
        return move_string

    out_components = [components[0]]
    for component in components[1:]:
        if feed_regex.match(component):
            out_components.append('F%d' % feedrate)
            continue
        # If Z is overridden, remove it from the original components.
        if not z_override is None:
            if z_regex.match(component):
                continue
        if reduce((lambda a, b : a or b), (component.startswith(delete_prefix)
            for delete_prefix in delete_prefixes)):
            continue
        out_components.append(component)

    # Add the Z component if its override is set.
    if not z_override is None:
        out_components.append('Z%f' % z_override)

    return ' '.join(out_components)

class Processor(object):
    def __init__(self):
        self.active_tool = None
        self.line_processors = [self.process_tool_change, self.process_z_move]
        self.current_z = 0

    def process_tool_change(self, line):
        """Determines if this line is a tool change line, and updates state
        accordingly."""
        match = tool_regex.match(line)
        if match is None:
            return False
        self.active_tool = int(match.groupdict()['tool'])
        return True

    def process_z_move(self, line):
        match = move_z_regex.match(line)
        if match is None:
            return False
        self.current_z = float(match.groupdict()['z_coordinate'])
        return True

    def process_line(self, line):
        for line_processor in self.line_processors:
            line_processor(line.strip())

    def process_lines(self, lines):
        for line in lines:
            self.process_line(line)


class TempProcessor(Processor):
    def __init__(self):
        super(TempProcessor, self).__init__()
        self.line_processors.append(self.process_temp_change)
        self.idle_temps = {}

    def process_temp_change(self, line):
        """Determines if this line is a temperature change line, and updates
        state accordingly."""
        match = temp_regex.match(line)
        if match is None:
            return False
        extruder_string = match.groupdict()['tool']
        if extruder_string is None:
            extruder = self.active_tool
        else:
            extruder = int(extruder_string)
        temperature = int(match.groupdict()['temp'])

        if temperature == 0:
            return True

        if not self.idle_temps.has_key(extruder):
            self.idle_temps[extruder] = temperature
            return True

        old_min_temp = self.idle_temps[extruder]
        if old_min_temp > temperature:
            self.idle_temps[extruder] = temperature
        return True

# TODO: Make blocks dictionaries.
class BlockProcessor(Processor):
    def __init__(self):
        super(BlockProcessor, self).__init__()
        self.line_processors.append(self.process_blocks)

        # Blocks are (state, active_tool, [lines], finish_z)
        self.blocks = [] #[(S_IDLE, 0, [], 0)]

        # Current block state. Allows modifying at any point before committing
        # to the block list.
        self.current_block_lines = []
        self.current_block_state = S_IDLE
        self.current_block_active_tool = 0
        # Store the finishing z position so that we don't collide into
        # previously printed material when wiping.
        self.current_block_finish_z = 0

    def process_blocks(self, line):
        for re, state in compiled_re_state_table:
            if re.match(line):
                # Append the previous current block before starting work on the
                # new one.
                self.blocks.append((self.current_block_state,
                                    self.current_block_active_tool,
                                    self.current_block_lines,
                                    self.current_block_finish_z))

                self.current_block_lines = []
                self.current_block_state = state
                self.current_block_active_tool = self.active_tool

                #self.blocks.append((state, self.active_tool, [], self.current_z))
        self.current_block_finish_z = self.current_z
        self.current_block_lines.append(line)

    def get_blocks(self):
        return self.blocks + [(self.current_block_state,
                               self.current_block_active_tool,
                               self.current_block_lines,
                               self.current_block_finish_z)]

class PrimeRetraceProcessor(Processor):
    def __init__(self, idle_temps, feed_override, active_tool, z_override):
        super(PrimeRetraceProcessor, self).__init__()
        self.feed_override = feed_override
        self.z_override = z_override

        temp_ramp_line = "M104 T%d S%d" % (active_tool, idle_temps[active_tool])
        self.lines = [";WIPE-PRIME-TOWER", temp_ramp_line]

        self.line_processors.append(self.process_prime_line)

        self.seen_move_line = False

    def process_prime_line(self, line):
        # Up until the first move command, rewrite normally.
        if not self.seen_move_line:
            # Make sure it's a move command with X or Y coordinate (so that
            # it's not just a feedrate config G command)
            if line.startswith('G') and ('X' in line or 'Y' in line):
                # At the first move command, rewrite normally.
                self.seen_move_line = True
                self.lines.append(rewrite_move(line, self.feed_override * 10, None))
                # Repeat the first move command, lowering to the correct Z.
                self.lines.append(rewrite_move(line, self.feed_override, self.z_override))
                return

            # For lines before we jog down in Z, we can move fast(er).
            self.lines.append(rewrite_move(line, self.feed_override * 10, None))
            return
        # TODO: Feed rate override, no extruder moves, no Z
        self.lines.append(rewrite_move(line, self.feed_override, None))
        

def write_blocks(blocks, output):
    for block in blocks:
        try:
            state, active_tool, lines, finish_z = block
        except Exception as e:
            print(block)
        for line in lines:
            output.write(line + os.linesep)

def modify_blocks(blocks, feedrate_override, idle_temps):
    output_blocks = []
    last_prime_block = None
    for block in blocks:
        state, active_tool, lines, finish_z = block
        # Cache the last prime block for insertion after extruder end
        if state == S_PRIME_BLOCK:
            last_prime_block = block
            output_blocks.append(block)
            continue

        if state == S_END_EXTRUDER:
            last_prime_block_active_tool = last_prime_block[1]
            last_prime_block_z_override = last_prime_block[3]
            print("End extruder block on T%d, last prime block on T%d at Z%f" %
                    (active_tool, last_prime_block_active_tool,
                        last_prime_block_z_override))

            # assert(last_prime_block_active_tool == active_tool)
            # idle temps and feedrate are global; active tool comes from
            # last_prime_block
            prime_retrace_processor = PrimeRetraceProcessor(idle_temps,
                feedrate_override, last_prime_block_active_tool,
                last_prime_block_z_override)
            # Process the last prime block through the prime retrace processor.
            # We assume that the last prime block is the correct prime block
            # for the part slice that was just printed by the nozzle being
            # swapped out. This assumption is correct for all but the first
            # layer on Cura 4.1.0 because the prime tower is printed with one
            # complete base layer from T0, followed by brims of T1 --> Tn.
            # Subsequent layers include material on this initial T0 layer in
            # concentric square perimeters. The first layer is not annotated
            # with prime section comments for each tool brim, thus we cannot
            # use one trick to separate the individual toolpaths. We ignore
            # this for the purpose of this demo algorithm.
            prime_retrace_processor.process_lines(last_prime_block[2])
            # Add the processed wipe block
            output_blocks.append((S_PRIME_BLOCK, last_prime_block_active_tool,
                prime_retrace_processor.lines, last_prime_block_z_override))
            # Add the end extruder block
            output_blocks.append(block)
            continue

        output_blocks.append(block)
    return output_blocks
            

def add_suffix(filename):
  """Adds '.postprocessed' into a filename, between the basename and the
  suffix."""
  fn,ext = os.path.splitext(filename)
  return "%s.%s%s" % (fn, 'postprocessed', ext)

if __name__ == '__main__':
    with open(sys.argv[1], 'r') as infile:
        temp_processor = TempProcessor()
        block_processor = BlockProcessor()
        lines = infile.readlines()
        # Get the idle temperatures
        temp_processor.process_lines(lines)
        # Get the blocks from the original gcode file
        block_processor.process_lines(lines)

        print(temp_processor.idle_temps)
    
        # Modify blocks (copy prime blocks to extruder end positions, overriding
        # feed rate and prepending temp ramp down)
        modified_blocks = modify_blocks(blocks=block_processor.get_blocks(),
            feedrate_override=300, idle_temps=temp_processor.idle_temps)
    
        with open(add_suffix(sys.argv[1]), 'w+') as outfile:
            write_blocks(modified_blocks, outfile)
    
