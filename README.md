# Processor Design Task 3 - Memory Hierarchy Simulation

This project implements a clock-driven memory hierarchy simulator for:

SSD -> DRAM -> L3 -> L2 -> L1 -> CPU


## Files

- `Task3_Zihan.py` - main simulator and demo workload
- `Task3_Output.txt` - sample output produced by the demo

## How to Run

```bash
python3 Task3_Zihan.py
```

The script will:
1. build a sample hierarchy
2. preload SSD with 12 example instructions
3. execute a sequence of reads and writes
4. flush dirty data downward
5. print a full report

## Default Demo Configuration

- SSD size = 32 instructions
- DRAM size = 16 instructions
- L3 size = 8 instructions
- L2 size = 4 instructions
- L1 size = 2 instructions
- Replacement policy = LRU
- Transfer latencies:
  - SSD = 0
  - DRAM = 6
  - L3 = 4
  - L2 = 2
  - L1 = 1
- The demo uses `LRU`, This implementation supports: LRU, FIFO, RANDOM

## How to Change the Configuration

Edit the `build_demo_simulator()` function.


