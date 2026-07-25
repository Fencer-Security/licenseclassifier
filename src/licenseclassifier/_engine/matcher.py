"""Pure-Python port of licensecheck internal/match/rematch.go (+ regexp.go).

Compiles reSyntax trees to a word-level regexp bytecode, combines them, and runs a
Thompson NFA with a lazily-built/memoized DFA over word IDs — including the wildcard
"cut" optimization and the context-sensitive spell checking (join/split/single-edit,
c<->copyright). Match is leftmost-longest, non-overlapping."""

from __future__ import annotations

from array import array

from licenseclassifier._engine.dictionary import ANY_WORD, to_fold
from licenseclassifier._engine.resyntax import (
    OP_ALTERNATE,
    OP_CONCAT,
    OP_EMPTY,
    OP_QUEST,
    OP_WILD,
    OP_WORDS,
    ReSyntax,
    leading_phrases,
)

# instruction opcodes
INST_WORD = 1
INST_ANY = 2
INST_ALT = 3
INST_JUMP = 4
INST_MATCH = 5
INST_CUT = 6


class _Compile:
    def __init__(self) -> None:
        self.prog: list[list[int]] = []
        self.end_pattern = True
        self.cut: list[list[int]] = []  # each [start, trigger]

    def compile(self, re: ReSyntax) -> None:
        op = re.op
        if op == OP_EMPTY:
            return
        if op == OP_WORDS:
            for w in re.w:
                self.prog.append([INST_WORD, w])
                self.reduce_cut()
            if self.end_pattern:
                self.compile_cuts()
            return
        if op == OP_CONCAT:
            end_index = len(re.sub)
            if self.end_pattern:
                while end_index > 0 and _can_match_empty(re.sub[end_index - 1]):
                    end_index -= 1
            for i, sub in enumerate(re.sub):
                self.end_pattern = i >= end_index
                self.compile(sub)
            return
        if op == OP_QUEST:
            alt = len(self.prog)
            self.prog.append([INST_ALT, 0])
            cut = self.cut
            end_pattern = self.end_pattern
            self.compile(re.sub[0])
            if end_pattern:
                self.compile_cuts()
            self.cut = self.merge_cut(cut, self.cut)
            self.prog[alt][1] = len(self.prog) - (alt + 1)
            return
        if op == OP_ALTERNATE:
            cut = self.cut
            end_pattern = self.end_pattern
            new_cut: list[list[int]] = []
            alts: list[int] = []
            jumps: list[int] = []
            for i, sub in enumerate(re.sub):
                if i + 1 < len(re.sub):
                    alts.append(len(self.prog))
                    self.prog.append([INST_ALT, 0])
                self.cut = cut
                self.end_pattern = end_pattern
                self.compile(sub)
                new_cut = self.merge_cut(new_cut, self.cut)
                if i + 1 < len(re.sub):
                    jumps.append(len(self.prog))
                    self.prog.append([INST_JUMP, 0])
            self.cut = new_cut
            for i, a in enumerate(alts):
                self.prog[a][1] = (jumps[i] + 1) - (a + 1)
            end = len(self.prog)
            for j in jumps:
                self.prog[j][1] = end - (j + 1)
            return
        if op == OP_WILD:
            self.compile_cuts()
            start = len(self.prog)
            end = len(self.prog) + int(re.n) * 2
            for _ in range(int(re.n)):
                self.prog.append([INST_ALT, end - (len(self.prog) + 1)])
                self.prog.append([INST_ANY, 0])
            if re.n > 3:
                self.cut = [[start, 3]]
            return
        raise ValueError(f"unexpected op {op}")

    def compile_cuts(self) -> None:
        for cut in self.cut:
            self.compile_cut(cut)
        self.cut = []

    def compile_cut(self, cut: list[int]) -> None:
        self.prog.append([INST_CUT, cut[0] - (len(self.prog) + 1)])

    def reduce_cut(self) -> None:
        nxt = []
        for cut in self.cut:
            cut[1] -= 1
            if cut[1] == 0:
                self.compile_cut(cut)
                continue
            nxt.append(cut)
        self.cut = nxt

    def merge_cut(self, cut1: list[list[int]], cut2: list[list[int]]) -> list[list[int]]:
        if not cut1:
            return cut2
        if not cut2:
            return cut1
        lst = sorted(cut1 + cut2, key=lambda c: (c[0], -c[1]))
        out: list[list[int]] = []
        for cut in lst:
            if not out or out[-1][0] != cut[0]:
                out.append(cut)
        return out


def _can_match_empty(re: ReSyntax) -> bool:
    if re.op == OP_ALTERNATE:
        return any(_can_match_empty(s) for s in re.sub)
    if re.op == OP_CONCAT:
        for s in re.sub:
            if not _can_match_empty(s):
                return False
        return True
    if re.op == OP_WORDS:
        return len(re.w) == 0
    return True


def compile_syntax(re: ReSyntax, m: int) -> list[list[int]]:
    c = _Compile()
    c.compile(re)
    c.compile_cuts()
    c.prog.append([INST_MATCH, m])
    return c.prog


def compile_multi(progs: list[list[list[int]]]) -> list[list[int]]:
    prog: list[list[int]] = []
    for i, prog1 in enumerate(progs):
        alt = -1
        if i + 1 < len(progs):
            alt = len(prog)
            prog.append([INST_ALT, 0])
        for inst in prog1:
            if inst[0] == INST_MATCH:
                prog.append([INST_MATCH, i])
            else:
                prog.append([inst[0], inst[1]])
        if alt >= 0:
            prog[alt][1] = len(prog) - (alt + 1)
    return prog


def _add(ops, args, out, inset, pc) -> None:
    stack = [pc]
    while stack:
        pc = stack.pop()
        if pc in inset:
            continue
        inset.add(pc)
        out.append(pc)
        op = ops[pc]
        if op == INST_ALT:
            stack.append(pc + 1 + args[pc])
            stack.append(pc + 1)
        elif op == INST_JUMP:
            stack.append(pc + 1 + args[pc])
        elif op == INST_CUT:
            stack.append(pc + 1)


def _trim(ops, args, pcs) -> tuple:
    cuts = sorted(pc + 1 + args[pc] for pc in pcs if ops[pc] == INST_CUT)
    save = []
    for pc in sorted(pcs):
        op = ops[pc]
        if op == INST_WORD or op == INST_ANY or op == INST_MATCH:
            while cuts and pc > cuts[0] + 1 + args[cuts[0]]:
                cuts = cuts[1:]
            if cuts and cuts[0] <= pc <= cuts[0] + 1 + args[cuts[0]]:
                continue
            save.append(pc)
    return tuple(save)


def _nfa_start(ops, args) -> tuple:
    out: list[int] = []
    inset: set[int] = set()
    _add(ops, args, out, inset, 0)
    return _trim(ops, args, out)


def _nfa_next(ops, args, state, w) -> tuple:
    out: list[int] = []
    inset: set[int] = set()
    for pc in state:
        op = ops[pc]
        if op == INST_ANY:
            _add(ops, args, out, inset, pc + 1)
        elif op == INST_WORD and args[pc] == w:
            _add(ops, args, out, inset, pc + 1)
    return _trim(ops, args, out)


def _can_misspell(want: str, have: str) -> bool:
    lw, lh = len(want), len(have)
    if lw - 1 <= lh <= lw + 1 and (lh >= 4 or lw >= 4):
        i = 0
        while i < lh and i < lw and want[i] == have[i]:
            i += 1
        j = 0
        while j < lh and j < lw and want[lw - 1 - j] == have[lh - 1 - j]:
            j += 1
        if i + j >= lw - 1 and i + j >= lh - 1:
            return True
    if (want == "c" or want == "copyright") and (have == "c" or have == "copyright" or have == "©"):
        return True
    return False


def _can_misspell_join(want: str, have1: str, have2: str) -> bool:
    return len(want) == len(have1) + len(have2) and want.startswith(have1) and want[len(have1) :] == have2


class MultiRE:
    def __init__(self, lres: list[tuple], dict_) -> None:
        # lres: list of (id_index, syntax); id_index is the match value to report
        progs = [compile_syntax(syn, idx) for idx, syn in lres]
        prog = compile_multi(progs)
        start: set[tuple[int, int]] = set()
        for _idx, syn in lres:
            for p in leading_phrases(syn):
                start.add(p)
        ops = array("i", [ins[0] for ins in prog])
        args = array("i", [ins[1] for ins in prog])
        self._init(ops, args, start, _nfa_start(ops, args), dict_)

    @classmethod
    def from_compiled(cls, ops, args, start, start_state, dict_) -> MultiRE:
        """Rebuild a MultiRE from a prebuilt (already-compiled) program."""
        self = cls.__new__(cls)
        self._init(ops, args, start, start_state, dict_)
        return self

    def _init(self, ops, args, start, start_state, dict_) -> None:
        # ops/args are parallel int32 arrays: the compiled bytecode program.
        self.ops = ops
        self.args = args
        self.dict = dict_
        self.start = start
        self._start_state = start_state
        self._info: dict[tuple, tuple] = {}

    def _state_info(self, state: tuple):
        info = self._info.get(state)
        if info is not None:
            return info
        ops = self.ops
        args = self.args
        m = -1
        for pc in state:
            if ops[pc] == INST_MATCH:
                a = args[pc]
                if m == -1 or a < m:
                    m = a
        nmap: dict[int, tuple] = {}
        any_next = None
        seen = set()
        for pc in state:
            op = ops[pc]
            if op == INST_ANY:
                if any_next is None:
                    any_next = _nfa_next(ops, args, state, ANY_WORD)
            elif op == INST_WORD:
                w = args[pc]
                if w not in seen:
                    seen.add(w)
                    nmap[w] = _nfa_next(ops, args, state, w)
        wl = sorted(seen)
        info = (m, wl, nmap, any_next)
        self._info[state] = info
        return info

    def _match_dfa(self, text: str, words: list, start: int) -> tuple[int, int]:
        dwords = self.dict.list
        match = -1
        end = 0
        state = self._start_state
        n = len(words)
        i = start
        while i < n:
            w_id = words[i][0]
            m, wl, nmap, any_next = self._state_info(state)
            if m >= 0:
                match = m
                end = i - start
            nxt = nmap.get(w_id)
            if nxt is not None:
                state = nxt
                i += 1
                continue
            # spell check
            have = to_fold(text[words[i][1] : words[i][2]])
            have2 = to_fold(text[words[i + 1][1] : words[i + 1][2]]) if i + 1 < n else ""
            advanced = False
            for dw in wl:
                want = dwords[dw]
                dnext = nmap[dw]
                if _can_misspell_join(want, have, have2):
                    state = dnext
                    i += 2
                    advanced = True
                    break
                if len(have) > len(want) and have[: len(want)] == want:
                    rest = have[len(want) :]
                    m2, wl2, nmap2, any2 = self._state_info(dnext)
                    next2 = any2
                    for dw2 in wl2:
                        if dwords[dw2] == rest:
                            next2 = nmap2[dw2]
                    if next2 is not None:
                        if m2 >= 0:
                            match = m2
                            end = i - start
                        state = next2
                        i += 1
                        advanced = True
                        break
                if _can_misspell(want, have):
                    state = dnext
                    i += 1
                    advanced = True
                    break
            if advanced:
                continue
            if any_next is None:
                return match, end
            state = any_next
            i += 1
        m, _, _, _ = self._state_info(state)
        if m >= 0:
            match = m
            end = n - start
        return match, end

    def match(self, text: str) -> tuple[list, list]:
        """Return (words, matches) where matches are (id_index, start_word, end_word)."""
        words = self.dict.split(text)
        n = len(words)
        result: list[tuple[int, int, int]] = []
        p0 = p1 = -1  # BadWord sentinels
        i = 0
        while i < n:
            p0, p1 = p1, words[i][0]
            if (p0, p1) in self.start:
                match, end = self._match_dfa(text, words, i - 1)
                if match >= 0 and end > 0:
                    end += i - 1
                    result.append((match, i - 1, end))
                    i = end - 1
                    p0 = -1
            i += 1
        return words, result
