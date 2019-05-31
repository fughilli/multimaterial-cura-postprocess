from __future__ import print_function

import sys
import re
import os

S_INIT = 0
S_PRIME_BLOCK = 1
S_PRIME_WIPE_BLOCK = 2
S_START_EXTRUDER = 3
S_END_EXTRUDER = 4
S_PART = 5
S_END = 6

raw_re_state_table = [(';TYPE:WALL.*', S_PART),
                      (';TYPE:PRIME-TOWER', S_PRIME_BLOCK),
                      ('; EXTRUDER START HOME', S_START_EXTRUDER),
                      ('; EXTRUDER END HOME', S_END_EXTRUDER)]

compiled_re_state_table = [(re.compile(k), v) for k, v in raw_re_state_table]

tool_regex = re.compile('^T(?P<tool>[0-9]*)$')
temp_regex = re.compile('^M10[49]( T(?P<tool>[0-9]*))? S(?P<temp>[0-9]*)$')
move_z_regex = re.compile('^G[01] [^Z]*Z(?P<z_coordinate>[0-9](.[0-9]*)?).*$')
# move_regex = re.compile('^(?P<gcode>G[01]) ((?P<feed>F[0-9]*)|(?P<coordinate>[XYZ][0-9].?[0-9]*)')

move_regex = re.compile('^G[01]$')
feed_regex = re.compile('^F([0-9].)?[0-9]*$')
z_regex = re.compile('^Z([0-9].)?[0-9]*$')


def parse_gcode(line):
    """Parses gcode line into a tuple of (operation, arguments)."""
    if ';' in line:
        line = line[:line.find(';')]
    line = line.strip()
    if line == '':
        return (None, {})
    parts = line.split(' ')
    op = parts[0]
    args = parts[1:]
    args_dict = {}
    for arg in args:
        try:
            args_dict[arg[0]] = int(arg[1:])
        except ValueError as e:
            args_dict[arg[0]] = float(arg[1:])
    return (op, args_dict)


def make_gcode(op, args_dict):
    """Makes a gcode line out of an operation and arguments."""
    args = [op]
    for arg_key, arg_value in args_dict.items():
        args.append("%s%s" % (arg_key, arg_value))
    return ' '.join(args)


def rewrite(gcode_string, op_regex, override={}, force={}, delete=[]):
    """Rewrites a gcode line.

    Args:
      op_regex(re): A regular expression to match `gcode_string` op against. If
                    the op does not match, `gcode_string` is returned as-is.
      override(Dict[str, Any]): A dictionary of arguments to be overridden; if
                                the arguments are present in `gcode_string`,
                                their values are substituted for the provided
                                values in `override`. Otherwise, the `override`
                                values are ignored.
      force(Dict[str, Any]): Similar to `override`, but these arguments are
                             included in the rewritten gcode regardless of
                             whether or not they were present in `gcode_string`.
      delete(List[str]): A list of arguments to be removed from `gcode_string`.
    Returns:
      str: A new gcode line.
    """
    op, args_dict = parse_gcode(gcode_string)
    if op is None:
        return gcode_string
    if not op_regex.match(op):
        # not a matching command, skip it
        return gcode_string

    for k, v in override.items():
        if args_dict.has_key(k):
            args_dict[k] = v

    for k, v in force.items():
        args_dict[k] = v

    for k in delete:
        args_dict.pop(k, None)

    return make_gcode(op, args_dict)


class Processor(object):

    def __init__(self):
        self.active_tool = 0
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
        self.printing_temps = {}

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

        self.update_idle_temps(extruder, temperature)
        self.update_printing_temps(extruder, temperature)
        return True

    def update_idle_temps(self, extruder, temperature):
        if not self.idle_temps.has_key(extruder):
            self.idle_temps[extruder] = temperature
            return

        old_min_temp = self.idle_temps[extruder]
        if old_min_temp > temperature:
            self.idle_temps[extruder] = temperature
        return

    def update_printing_temps(self, extruder, temperature):
        if not self.printing_temps.has_key(extruder):
            self.printing_temps[extruder] = temperature
            return

        old_max_temp = self.printing_temps[extruder]
        if old_max_temp < temperature:
            self.printing_temps[extruder] = temperature
        return


class Block(object):

    def __init__(self,
                 lines,
                 state=S_INIT,
                 start_z=0,
                 finish_z=0,
                 active_tool=0):
        self.lines = lines
        self.state = state
        self.start_z = start_z
        # Store the finishing z position so that we don't collide into
        # previously printed material when wiping.
        self.finish_z = finish_z
        self.active_tool = active_tool

    def copy(self):
        return Block(self.lines[:], self.state, self.start_z, self.finish_z,
                     self.active_tool)

    def copy_empty_lines(self):
        return Block([], self.state, self.start_z, self.finish_z,
                     self.active_tool)

    def remove_matching_ops(self, op_regex_string):
        op_regex = re.compile(op_regex_string)
        new_lines = []
        for line in self.lines:
            op, args = parse_gcode(line)
            if op is None:
                new_lines.append(line)
                continue
            if op_regex.match(op):
                continue
            new_lines.append(line)
        self.lines = new_lines

    def add_temperatures(self, wait, idle_temps, printing_temps):
        # (idle|printing)_temps are dicts of tool_number -> temperature
        new_lines = []
        extruding = False
        for line in self.lines:
            op, args = parse_gcode(line)
            if 'E' in args.keys() and not extruding:
                new_lines.append('M10%s T%d S%d' %
                                 (('9' if wait else '4'), self.active_tool,
                                  printing_temps[self.active_tool]))
                extruding = True
            new_lines.append(line)
        self.lines = new_lines


class BlockProcessor(Processor):

    def __init__(self):
        super(BlockProcessor, self).__init__()
        self.line_processors.append(self.process_blocks)

        self.blocks = []

        # Current block state. Allows modifying at any point before committing
        # to the block list.
        self.current_block = Block([])

    def process_blocks(self, line):
        for re, state in compiled_re_state_table:
            if re.match(line):
                # Append the previous current block before starting work on the
                # new one.
                self.blocks.append(self.current_block)

                self.current_block = Block(lines=[],
                                           state=state,
                                           start_z=self.current_z,
                                           active_tool=self.active_tool)

        self.current_block.finish_z = self.current_z
        self.current_block.lines.append(line)

    def get_blocks(self):
        return self.blocks + [self.current_block]


class PrimeRetraceProcessor(Processor):

    def __init__(self, idle_temps, feed_override, active_tool, z_override):
        super(PrimeRetraceProcessor, self).__init__()
        self.feed_override = feed_override
        self.z_override = z_override

        self.lines = [";WIPE-PRIME-TOWER"]

        for tool, idle_temp in idle_temps.items():
            self.lines.append("M104 T%d S%d" % (tool, idle_temp))

        self.active_idle_line = "M109 T%d S%d" % (active_tool,
                                                  idle_temps[active_tool])

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
                self.lines.append(
                    rewrite_move(line, self.feed_override * 15, None))
                # Repeat the first move command, lowering to the correct Z.
                self.lines.append(
                    rewrite_move(line, self.feed_override, self.z_override))
                return

            # For lines before we jog down in Z, we can move fast(er).
            self.lines.append(rewrite_move(line, self.feed_override * 15, None))
            return
        # TODO: Feed rate override, no extruder moves, no Z
        self.lines.append(rewrite_move(line, self.feed_override, None))

    def get_lines(self):
        # Cut the last 5 lines. This is a hack to remove spurious jogs back to
        # the part at the end of the prime tower.
        return self.lines[:-5] + [self.active_idle_line]


def rewrite_move(line, feed_override, z_force):
    if z_force is None:
        return rewrite(line,
                       move_regex,
                       override={'F': feed_override},
                       delete=['E', 'Z'])
    return rewrite(line,
                   move_regex,
                   override={'F': feed_override},
                   force={'Z': z_force},
                   delete=['E'])


class PrimeProcessor(Processor):

    def __init__(self, active_tool, idle_temps, printing_temps):
        super(PrimeProcessor, self).__init__()
        self.lines = [";PRE-PRIME-TOWER"]
        self.lines.append("M109 T%d S%d" %
                          (active_tool, printing_temps[active_tool]))

        self.line_processors.append(self.process_prime_line)

    def process_prime_line(self, line):
        self.lines.append(line)

    def get_lines(self):
        return self.lines


def write_blocks(blocks, output):
    for block in blocks:
        for line in block.lines:
            output.write(line + '\r\n')


def append_block(block_list, block):
    #print("Appending block %d of length %d" % (block.state, len(block.lines)))
    return block_list + [block]


def modify_blocks(blocks, feedrate_override, idle_temps, printing_temps):
    output_blocks = []
    last_prime_block = Block([])
    # Hack to make sure that we get the right temperature ramps for the
    # raft/initial prime tower layer
    seen_first_part_block_tools = []
    for block in blocks:
        # Remove temperature lines from all but the initial block. We will
        # completely override them.
        if block.state != S_INIT:
            if set(seen_first_part_block_tools) == set(idle_temps.keys()):
                block.remove_matching_ops('M10[49]')

        if block.state == S_PRIME_BLOCK:
            # Cache the last prime block for insertion after extruder end
            last_prime_block = block.copy()

            # Process the prime block. This adds a temperature ramp and wait to
            # the beginning of the block for the active tool.
            prime_processor = PrimeProcessor(block.active_tool, idle_temps,
                                             printing_temps)
            prime_processor.process_lines(block.lines)

            # Copy the block, adding the processed lines, and append to the
            # block list.
            new_block = block.copy_empty_lines()
            new_block.lines = prime_processor.get_lines()
            new_block.add_temperatures(True, idle_temps, printing_temps)
            output_blocks = append_block(output_blocks, new_block)
            continue

        if block.state == S_PART:
            new_block = block.copy()
            new_block.add_temperatures(False, idle_temps, printing_temps)
            output_blocks = append_block(output_blocks, new_block)
            seen_first_part_block_tools = set(
                list(seen_first_part_block_tools) + [new_block.active_tool])
            continue

        if block.state == S_END_EXTRUDER:
            print("End extruder block on T%d, last prime block on T%d at Z%f" %
                  (block.active_tool, last_prime_block.active_tool,
                   last_prime_block.finish_z))

            # assert(last_prime_block_active_tool == active_tool)
            # idle temps and feedrate are global; active tool comes from
            # last_prime_block
            prime_retrace_processor = PrimeRetraceProcessor(
                idle_temps, feedrate_override, last_prime_block.active_tool,
                last_prime_block.finish_z)
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
            prime_retrace_processor.process_lines(last_prime_block.lines)
            # Add the processed wipe block
            prime_wipe_block = last_prime_block.copy_empty_lines()
            prime_wipe_block.state = S_PRIME_WIPE_BLOCK
            prime_wipe_block.active_tool = block.active_tool
            prime_wipe_block.lines = prime_retrace_processor.get_lines()
            #output_blocks.append(prime_wipe_block)
            output_blocks = append_block(output_blocks, prime_wipe_block)
            # Add the end extruder block
            #output_blocks.append(block)
            output_blocks = append_block(output_blocks, block)
            continue

        #output_blocks.append(block)
        output_blocks = append_block(output_blocks, block)

    end_block = Block([])
    end_block.state = S_END
    end_block.lines = [("M104 T%d S0" % k) for k, v in idle_temps.items()]
    #output_blocks.append(end_block)
    output_blocks = append_block(output_blocks, end_block)

    return output_blocks


def add_suffix(filename):
    """Adds '.postprocessed' into a filename, between the basename and the
  suffix."""
    fn, ext = os.path.splitext(filename)
    return "%s.%s%s" % (fn, 'postprocessed', ext)
