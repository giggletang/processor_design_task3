# CSC 6210 - Processor Design Task 3
# Zihan Tang

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import copy

# -----------------------------------------------------------------------------
# Helper function: Force a value to behave like a 32-bit unsigned instruction
# -----------------------------------------------------------------------------
def to_u32(value: int) -> int:
    return value & 0xFFFFFFFF

# -----------------------------------------------------------------------------
# Basic data unit stored inside memory levels
# -----------------------------------------------------------------------------
@dataclass
class CacheLine:
    address: int
    value: int
    dirty: bool = False
    last_access: int = 0
    inserted_at: int = 0

    def clone(self) -> "CacheLine":
        return copy.deepcopy(self)

# -----------------------------------------------------------------------------
# Generic storage level: SSD, DRAM, L3, L2, or L1
# -----------------------------------------------------------------------------
class MemoryLevel:

    def __init__(
        self,
        name: str,
        capacity: int,
        latency: int,
        replacement_policy: str = "LRU",
    ) -> None:
        self.name = name
        self.capacity = capacity
        self.latency = latency
        self.replacement_policy = replacement_policy.upper()

        # Dictionary mapping address -> CacheLine
        self.lines: Dict[int, CacheLine] = {}

    def __len__(self) -> int:
        return len(self.lines)

    def contains(self, address: int) -> bool:
        return address in self.lines

    def get(self, address: int, cycle: int) -> Optional[CacheLine]:
        line = self.lines.get(address)
        if line is not None:
            line.last_access = cycle
        return line

    def insert_or_update(self, line: CacheLine, cycle: int) -> Optional[CacheLine]:
        if line.address in self.lines:
            existing = self.lines[line.address]
            existing.value = to_u32(line.value)
            existing.dirty = line.dirty
            existing.last_access = cycle
            return None

        evicted = None
        if len(self.lines) >= self.capacity:
            evicted = self.evict_one()

        new_line = line.clone()
        new_line.value = to_u32(new_line.value)
        new_line.last_access = cycle
        new_line.inserted_at = cycle
        self.lines[new_line.address] = new_line
        return evicted

    def evict_one(self) -> CacheLine:
        if not self.lines:
            raise RuntimeError(f"Cannot evict from empty level {self.name}")

        if self.replacement_policy == "RANDOM":
            address = next(iter(self.lines.keys()))
        elif self.replacement_policy == "FIFO":
            address = min(self.lines.items(), key=lambda item: item[1].inserted_at)[0]
        else:  # default to LRU
            address = min(self.lines.items(), key=lambda item: item[1].last_access)[0]

        return self.lines.pop(address)

    def update_value(self, address: int, value: int, cycle: int, dirty: bool = True) -> None:
        if address not in self.lines:
            raise KeyError(f"Address {address} is not present in {self.name}")
        line = self.lines[address]
        line.value = to_u32(value)
        line.dirty = dirty
        line.last_access = cycle

    def mark_clean(self, address: int) -> None:
        if address in self.lines:
            self.lines[address].dirty = False

    def snapshot(self) -> List[str]:
        rows = []
        for addr in sorted(self.lines.keys()):
            line = self.lines[addr]
            rows.append(
                f"addr={addr:02d} value=0x{line.value:08X} dirty={line.dirty}"
            )
        return rows


# -----------------------------------------------------------------------------
# Objects used by the simulator to model activity over time
# -----------------------------------------------------------------------------
@dataclass
class Transfer:
    transfer_id: int
    src: str
    dst: str
    line: CacheLine
    remaining_cycles: int
    kind: str  # fill / writeback / flush
    request_id: Optional[int] = None

@dataclass
class RequestState:
    request_id: int
    op: str  # READ / WRITE
    address: int
    write_value: Optional[int]
    found_at: str
    path: List[Tuple[str, str]] = field(default_factory=list)
    next_step: int = 0
    completed: bool = False
    result: Optional[int] = None
    issue_cycle: int = 0
    complete_cycle: Optional[int] = None

# -----------------------------------------------------------------------------
# Main simulator
# -----------------------------------------------------------------------------
class MemoryHierarchySimulator:
    # Order is listed from lowest/slowest storage to highest/closest cache.
    order = ["SSD", "DRAM", "L3", "L2", "L1"]
    cache_levels = ["L3", "L2", "L1"]

    def __init__(
        self,
        ssd_size: int,
        dram_size: int,
        l3_size: int,
        l2_size: int,
        l1_size: int,
        latencies: Optional[Dict[str, int]] = None,
        replacement_policy: str = "LRU",
    ) -> None:
        # The assignment requires the hierarchy size order
        # SSD > DRAM > L3 > L2 > L1
        if not (ssd_size > dram_size > l3_size > l2_size > l1_size):
            raise ValueError("Hierarchy sizes must satisfy SSD > DRAM > L3 > L2 > L1")

        # Default transfer latencies if the user does not provide their own.
        latencies = latencies or {"SSD": 0, "DRAM": 6, "L3": 4, "L2": 2, "L1": 1}

        # Create one MemoryLevel object for each level in the hierarchy.
        self.levels: Dict[str, MemoryLevel] = {
            "SSD": MemoryLevel("SSD", ssd_size, latencies.get("SSD", 0), replacement_policy),
            "DRAM": MemoryLevel("DRAM", dram_size, latencies.get("DRAM", 6), replacement_policy),
            "L3": MemoryLevel("L3", l3_size, latencies.get("L3", 4), replacement_policy),
            "L2": MemoryLevel("L2", l2_size, latencies.get("L2", 2), replacement_policy),
            "L1": MemoryLevel("L1", l1_size, latencies.get("L1", 1), replacement_policy),
        }

        # Global simulation state.
        self.current_cycle = 0
        self.pending_transfers: List[Transfer] = []
        self.requests: Dict[int, RequestState] = {}
        self.next_transfer_id = 1
        self.next_request_id = 1

        # Logs for the final report.
        self.trace: List[str] = []
        self.movements: List[str] = []

        # Simple cache statistics.
        self.stats = {
            "L1_hits": 0,
            "L1_misses": 0,
            "L2_hits": 0,
            "L2_misses": 0,
            "L3_hits": 0,
            "L3_misses": 0,
            "reads": 0,
            "writes": 0,
        }

    def load_program(self, instructions: List[int]) -> None:
        if len(instructions) > self.levels["SSD"].capacity:
            raise ValueError("Program is larger than SSD capacity")

        self.levels["SSD"].lines.clear()
        for address, value in enumerate(instructions):
            self.levels["SSD"].lines[address] = CacheLine(
                address=address,
                value=to_u32(value),
                dirty=False,
                last_access=0,
                inserted_at=0,
            )

        self.trace.append(
            f"Initialized SSD with {len(instructions)} 32-bit instructions (addresses 0..{len(instructions)-1})."
        )

    def _record_cache_lookup(self, found_at: str) -> None:
        if found_at == "L1":
            self.stats["L1_hits"] += 1
            return
        self.stats["L1_misses"] += 1

        if found_at == "L2":
            self.stats["L2_hits"] += 1
            return
        self.stats["L2_misses"] += 1

        if found_at == "L3":
            self.stats["L3_hits"] += 1
            return
        self.stats["L3_misses"] += 1

    def _find_level_containing(self, address: int) -> str:
        for level_name in reversed(self.order):  # L1 -> SSD
            if self.levels[level_name].contains(address):
                self._record_cache_lookup(level_name)
                return level_name
        raise KeyError(f"Address {address} does not exist anywhere in the hierarchy")

    def _build_fill_path(self, found_at: str) -> List[Tuple[str, str]]:
        idx = self.order.index(found_at)
        path = []
        while idx < len(self.order) - 1:
            path.append((self.order[idx], self.order[idx + 1]))
            idx += 1
        return path

    def _schedule_transfer(
        self,
        src: str,
        dst: str,
        line: CacheLine,
        kind: str,
        request_id: Optional[int] = None,
    ) -> None:
    
        moved_line = line.clone()

        # Once dirty data finally reaches SSD, we consider it safely committed.
        if kind.lower() in {"writeback", "flush"} and dst == "SSD":
            moved_line.dirty = False

        transfer = Transfer(
            transfer_id=self.next_transfer_id,
            src=src,
            dst=dst,
            line=moved_line,
            remaining_cycles=max(1, self.levels[dst].latency),
            kind=kind,
            request_id=request_id,
        )
        self.next_transfer_id += 1
        self.pending_transfers.append(transfer)
        self.movements.append(
            f"Cycle {self.current_cycle:03d}: scheduled {kind.upper()} of addr {line.address} (0x{line.value:08X}) {src} -> {dst}, latency={transfer.remaining_cycles}"
        )

    def tick(self) -> None:
        self.current_cycle += 1
        finished: List[Transfer] = []

        for transfer in self.pending_transfers:
            transfer.remaining_cycles -= 1
            if transfer.remaining_cycles <= 0:
                finished.append(transfer)

        for transfer in finished:
            self.pending_transfers.remove(transfer)
            self._complete_transfer(transfer)

    def run_until_idle(self) -> None:
        while self.pending_transfers:
            self.tick()

    def _lower_level(self, level_name: str) -> Optional[str]:
        idx = self.order.index(level_name)
        return self.order[idx - 1] if idx > 0 else None

    def _complete_transfer(self, transfer: Transfer) -> None:
        dst_level = self.levels[transfer.dst]
        evicted = dst_level.insert_or_update(transfer.line, self.current_cycle)

        self.movements.append(
            f"Cycle {self.current_cycle:03d}: completed {transfer.kind.upper()} addr {transfer.line.address} {transfer.src} -> {transfer.dst}"
        )

        # If the destination level was full, one old line may be evicted.
        if evicted is not None:
            self.movements.append(
                f"Cycle {self.current_cycle:03d}: eviction from {transfer.dst} -> addr {evicted.address} value=0x{evicted.value:08X} dirty={evicted.dirty}"
            )

            # Dirty eviction means the lower level must be updated later.
            if evicted.dirty:
                lower = self._lower_level(transfer.dst)
                if lower is None:
                    raise RuntimeError("Dirty data reached below SSD, which should not happen")
                self._schedule_transfer(transfer.dst, lower, evicted, kind="writeback", request_id=None)

        # request_id=None means this was background traffic such as flush/writeback.
        if transfer.request_id is None:
            return

        req = self.requests[transfer.request_id]
        req.next_step += 1

        # If the line has not reached L1 yet, schedule the next legal upward step.
        if req.next_step < len(req.path):
            next_src, next_dst = req.path[req.next_step]
            latest_line = self.levels[next_src].get(req.address, self.current_cycle)
            if latest_line is None:
                raise RuntimeError(f"Expected address {req.address} in {next_src} before next transfer")
            self._schedule_transfer(next_src, next_dst, latest_line, kind="fill", request_id=req.request_id)
            return

        # If execution reaches here, the request has reached L1.
        l1 = self.levels["L1"]
        final_line = l1.get(req.address, self.current_cycle)
        if final_line is None:
            raise RuntimeError("Request should have the line in L1 after fill path completion")

        if req.op == "WRITE":
            # Write is performed in L1 and marked dirty.
            l1.update_value(req.address, req.write_value if req.write_value is not None else final_line.value, self.current_cycle, dirty=True)
            req.result = req.write_value
            self.trace.append(
                f"Cycle {self.current_cycle:03d}: WRITE completed for addr {req.address} in L1, new value=0x{req.result:08X}"
            )
        else:
            # Read simply returns the current value in L1.
            req.result = final_line.value
            self.trace.append(
                f"Cycle {self.current_cycle:03d}: READ completed for addr {req.address} in L1, value=0x{req.result:08X}"
            )

        req.completed = True
        req.complete_cycle = self.current_cycle

    def read(self, address: int) -> int:
        self.stats["reads"] += 1
        found_at = self._find_level_containing(address)
        request_id = self.next_request_id
        self.next_request_id += 1

        self.trace.append(
            f"Cycle {self.current_cycle:03d}: READ request for addr {address} -> found at {found_at}"
        )

        if found_at == "L1":
            line = self.levels["L1"].get(address, self.current_cycle)
            assert line is not None
            self.trace.append(
                f"Cycle {self.current_cycle:03d}: READ served immediately from L1, value=0x{line.value:08X}"
            )
            return line.value

        req = RequestState(
            request_id=request_id,
            op="READ",
            address=address,
            write_value=None,
            found_at=found_at,
            path=self._build_fill_path(found_at),
            issue_cycle=self.current_cycle,
        )
        self.requests[request_id] = req

        # Schedule the first movement in the path, then let time advance.
        first_src, first_dst = req.path[0]
        line = self.levels[first_src].get(address, self.current_cycle)
        if line is None:
            raise RuntimeError(f"Address {address} disappeared from {first_src}")
        self._schedule_transfer(first_src, first_dst, line, kind="fill", request_id=request_id)
        self.run_until_idle()
        return req.result if req.result is not None else 0

    def write(self, address: int, value: int) -> None:
        value = to_u32(value)
        self.stats["writes"] += 1
        found_at = self._find_level_containing(address)
        request_id = self.next_request_id
        self.next_request_id += 1

        self.trace.append(
            f"Cycle {self.current_cycle:03d}: WRITE request for addr {address} -> target value=0x{value:08X}, found at {found_at}"
        )

        if found_at == "L1":
            self.levels["L1"].update_value(address, value, self.current_cycle, dirty=True)
            self.trace.append(
                f"Cycle {self.current_cycle:03d}: WRITE completed immediately in L1, new value=0x{value:08X}"
            )
            return

        req = RequestState(
            request_id=request_id,
            op="WRITE",
            address=address,
            write_value=value,
            found_at=found_at,
            path=self._build_fill_path(found_at),
            issue_cycle=self.current_cycle,
        )
        self.requests[request_id] = req

        first_src, first_dst = req.path[0]
        line = self.levels[first_src].get(address, self.current_cycle)
        if line is None:
            raise RuntimeError(f"Address {address} disappeared from {first_src}")
        self._schedule_transfer(first_src, first_dst, line, kind="fill", request_id=request_id)
        self.run_until_idle()

    def flush_all(self) -> None:
        self.trace.append(f"Cycle {self.current_cycle:03d}: starting full flush of dirty lines")

        for upper, lower in [("L1", "L2"), ("L2", "L3"), ("L3", "DRAM"), ("DRAM", "SSD")]:
            upper_level = self.levels[upper]
            dirty_lines = [line.clone() for line in upper_level.lines.values() if line.dirty]
            for line in dirty_lines:
                upper_level.mark_clean(line.address)
                self._schedule_transfer(upper, lower, line, kind="flush")
                self.run_until_idle()

        self.trace.append(f"Cycle {self.current_cycle:03d}: flush completed")

    def configuration_summary(self) -> str:
        return (
            "\n".join(
                [
                    "Memory hierarchy configuration:",
                    f"  SSD  : size={self.levels['SSD'].capacity}, latency={self.levels['SSD'].latency}",
                    f"  DRAM : size={self.levels['DRAM'].capacity}, latency={self.levels['DRAM'].latency}",
                    f"  L3   : size={self.levels['L3'].capacity}, latency={self.levels['L3'].latency}",
                    f"  L2   : size={self.levels['L2'].capacity}, latency={self.levels['L2'].latency}",
                    f"  L1   : size={self.levels['L1'].capacity}, latency={self.levels['L1'].latency}",
                    f"  Replacement policy: {self.levels['L1'].replacement_policy}",
                ]
            )
        )

    def stats_summary(self) -> str:
        lines = [
            "Cache statistics:",
            f"  Reads={self.stats['reads']} Writes={self.stats['writes']}",
            f"  L1 hits={self.stats['L1_hits']} misses={self.stats['L1_misses']}",
            f"  L2 hits={self.stats['L2_hits']} misses={self.stats['L2_misses']}",
            f"  L3 hits={self.stats['L3_hits']} misses={self.stats['L3_misses']}",
        ]
        return "\n".join(lines)

    def final_state_summary(self) -> str:
        blocks = ["Final state of each memory level:"]
        for level_name in self.order:
            blocks.append(f"\n[{level_name}] ({len(self.levels[level_name])}/{self.levels[level_name].capacity})")
            snapshot = self.levels[level_name].snapshot()
            blocks.extend([f"  {row}" for row in snapshot] if snapshot else ["  <empty>"])
        return "\n".join(blocks)

    def full_report(self) -> str:
        sections = [
            self.configuration_summary(),
            "\nInstruction access trace:\n" + "\n".join(self.trace),
            "\nMovement of data across levels:\n" + "\n".join(self.movements),
            "\n" + self.stats_summary(),
            "\n" + self.final_state_summary(),
            f"\nTotal cycles elapsed: {self.current_cycle}",
        ]
        return "\n".join(sections)


# -----------------------------------------------------------------------------
# Demo setup and execution
# -----------------------------------------------------------------------------
def build_demo_simulator() -> MemoryHierarchySimulator:
    simulator = MemoryHierarchySimulator(
        ssd_size=32,
        dram_size=16,
        l3_size=8,
        l2_size=4,
        l1_size=2,
        latencies={"SSD": 0, "DRAM": 6, "L3": 4, "L2": 2, "L1": 1},
        replacement_policy="LRU",
    )

    # Example program:
    # address 0 stores 0x10000000
    # address 1 stores 0x10000001
    # ...
    # address 11 stores 0x1000000B
    program = [0x10000000 + i for i in range(12)]
    simulator.load_program(program)
    return simulator

def run_demo() -> None:
    sim = build_demo_simulator()

    operations = [
        ("read", 2),
        ("read", 5),
        ("read", 2),
        ("read", 9),
        ("write", 5, 0xDEADBEEF),
        ("read", 5),
        ("read", 1),
        ("read", 7),
        ("write", 9, 0xCAFEBABE),
        ("read", 9),
    ]

    for op in operations:
        if op[0] == "read":
            value = sim.read(op[1])
            print(f"READ  addr={op[1]:02d} -> 0x{value:08X}")
        elif op[0] == "write":
            sim.write(op[1], op[2])
            print(f"WRITE addr={op[1]:02d} <- 0x{op[2]:08X}")
        else:
            raise ValueError(f"Unsupported operation: {op[0]}")

    # At the end, flush all dirty lines so the lower levels are updated too.
    sim.flush_all()

    # Print the full report required for the project output.
    print("\n" + sim.full_report())


if __name__ == "__main__":
    run_demo()
