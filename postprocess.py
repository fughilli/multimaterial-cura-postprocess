import sys

import postprocess_lib

if __name__ == '__main__':
    with open(sys.argv[1], 'r') as infile:
        temp_processor = postprocess_lib.TempProcessor()
        block_processor = postprocess_lib.BlockProcessor()
        lines = infile.readlines()
        # Get the idle temperatures
        temp_processor.process_lines(lines)
        # Get the blocks from the original gcode file
        block_processor.process_lines(lines)

        #for block in block_processor.get_blocks():
        #    print("Block %d of size %d" % (block.state, len(block.lines)))

        print(temp_processor.idle_temps)

        # Modify blocks (copy prime blocks to extruder end positions, overriding
        # feed rate and prepending temp ramp down)
        modified_blocks = postprocess_lib.modify_blocks(
            blocks=block_processor.get_blocks(),
            feedrate_override=200,
            idle_temps=temp_processor.idle_temps,
            printing_temps=temp_processor.printing_temps)

        with open(postprocess_lib.add_suffix(sys.argv[1]), 'w+') as outfile:
            postprocess_lib.write_blocks(modified_blocks, outfile)
