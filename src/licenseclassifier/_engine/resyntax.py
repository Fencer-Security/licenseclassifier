"""Pure-Python port of licensecheck internal/match/resyntax.go.

Parses the LRE DSL into a regexp syntax tree (reSyntax). The tree construction is
identical to the Go parser; the strict positional checks are validation-only (the
builtin corpus is already valid) so they are omitted."""

from __future__ import annotations

from licenseclassifier._engine.dictionary import ANY_WORD, BAD_WORD, Dict

# reOp opcodes
OP_NONE = 1
OP_EMPTY = 2
OP_WORDS = 3
OP_CONCAT = 4
OP_ALTERNATE = 5
OP_WILD = 6
OP_QUEST = 7
OP_PSEUDO = 8
OP_LEFT_PAREN = 9
OP_VERTICAL_BAR = 10


class ReSyntax:
    __slots__ = ("op", "sub", "w", "n")

    def __init__(self, op: int, sub=None, w=None, n: int = 0) -> None:
        self.op = op
        self.sub = sub if sub is not None else []
        self.w = w if w is not None else []
        self.n = n


class _Parser:
    def __init__(self, d: Dict) -> None:
        self.dict = d
        self.stack: list[ReSyntax] = []

    def push(self, re: ReSyntax) -> ReSyntax:
        self.stack.append(re)
        return re

    def words(self, text: str, nxt: str) -> None:
        ws = self.dict.insert_split(text)
        if not ws:
            return
        # If the next operator is ??, keep the last word separate so ?? applies
        # only to it (there are no other operators that grab the last word).
        split_last = nxt == "??"
        main_words = ws[:-1] if split_last else ws
        if main_words:
            if self.stack and self.stack[-1].op == OP_WORDS:
                re = self.stack[-1]
            else:
                re = self.push(ReSyntax(OP_WORDS))
            for w in main_words:
                re.w.append(w[0])
        if split_last:
            self.stack.append(ReSyntax(OP_WORDS, w=[ws[-1][0]]))

    def vertical_bar(self) -> None:
        self.concat()
        if not self.swap_vertical_bar():
            self.push(ReSyntax(OP_VERTICAL_BAR))

    def swap_vertical_bar(self) -> bool:
        n = len(self.stack)
        if n >= 2 and self.stack[n - 2].op == OP_VERTICAL_BAR:
            self.stack[n - 2], self.stack[n - 1] = self.stack[n - 1], self.stack[n - 2]
            return True
        return False

    def right_paren(self) -> None:
        self.concat()
        if self.swap_vertical_bar():
            self.stack.pop()
        self.alternate()
        n = len(self.stack)
        if n < 2:
            raise ValueError("unexpected ))")
        re1 = self.stack[n - 1]
        re2 = self.stack[n - 2]
        self.stack = self.stack[: n - 2]
        if re2.op != OP_LEFT_PAREN:
            raise ValueError("unexpected ))")
        self.push(re1)

    def quest(self) -> None:
        n = len(self.stack)
        if n == 0:
            raise ValueError("missing argument to ??")
        sub = self.stack[n - 1]
        if sub.op >= OP_PSEUDO:
            raise ValueError("missing argument to ??")
        if sub.op == OP_QUEST:
            return
        self.stack[n - 1] = ReSyntax(OP_QUEST, sub=[sub])

    def concat(self) -> ReSyntax:
        i = len(self.stack)
        while i > 0 and self.stack[i - 1].op < OP_PSEUDO:
            i -= 1
        subs = self.stack[i:]
        self.stack = self.stack[:i]
        if not subs:
            return self.push(ReSyntax(OP_EMPTY))
        return self.push(self._collapse(OP_CONCAT, subs))

    def alternate(self) -> ReSyntax:
        i = len(self.stack)
        while i > 0 and self.stack[i - 1].op < OP_PSEUDO:
            i -= 1
        subs = self.stack[i:]
        self.stack = self.stack[:i]
        return self.push(self._collapse(OP_ALTERNATE, subs))

    def _collapse(self, op: int, subs: list[ReSyntax]) -> ReSyntax:
        if len(subs) == 1:
            return subs[0]
        re = ReSyntax(op)
        for sub in subs:
            if sub.op == op:
                re.sub.extend(sub.sub)
            else:
                re.sub.append(sub)
        return re


def re_parse(d: Dict, s: str) -> ReSyntax:
    p = _Parser(d)
    start = 0
    i = 0
    n = len(s)
    while i < n:
        if s.startswith("((", i):
            p.words(s[start:i], "((")
            p.push(ReSyntax(OP_LEFT_PAREN))
            i += 2
            start = i
        elif s.startswith("||", i):
            p.words(s[start:i], "||")
            p.vertical_bar()
            i += 2
            start = i
        elif s.startswith("))", i):
            p.words(s[start:i], "))")
            p.right_paren()
            i += 2
            start = i
        elif s.startswith("??", i):
            p.words(s[start:i], "??")
            p.quest()
            i += 2
            start = i
        elif s.startswith("__", i):
            j = i + 2
            while j < n and s[j].isdigit():
                j += 1
            if j == i + 2 or not s.startswith("__", j):
                i += 1
                continue
            cnt = int(s[i + 2 : j])
            p.words(s[start:i], "__")
            p.push(ReSyntax(OP_WILD, n=cnt))
            i = j + 2
            start = i
        elif s.startswith("//**", i):
            j = s.find("**//", i + 4)
            if j < 0:
                raise ValueError("opening //** without closing **//")
            p.words(s[start:i], "//** **//")
            i = j + 4  # j is the absolute index of "**//"; skip past it
            start = i
        else:
            i += 1

    p.words(s[start:], "")
    p.concat()
    if p.swap_vertical_bar():
        p.stack.pop()
    p.alternate()
    if len(p.stack) != 1:
        raise ValueError("missing )) at end")
    return p.stack[0]


def leading_phrases(re: ReSyntax) -> list[tuple[int, int]]:
    op = re.op
    if op == OP_WILD:
        return [(BAD_WORD, BAD_WORD), (ANY_WORD, BAD_WORD), (ANY_WORD, ANY_WORD)]
    if op == OP_EMPTY:
        return [(BAD_WORD, BAD_WORD)]
    if op == OP_WORDS:
        w = re.w
        if len(w) == 0:
            p = (BAD_WORD, BAD_WORD)
        elif len(w) == 1:
            p = (w[0], BAD_WORD)
        else:
            p = (w[0], w[1])
        return [p]
    if op == OP_QUEST:
        lst = leading_phrases(re.sub[0])
        for ph in lst:
            if ph[0] == BAD_WORD:
                return lst
        return lst + [(BAD_WORD, BAD_WORD)]
    if op == OP_ALTERNATE:
        phrases: list[tuple[int, int]] = []
        have = set()
        for sub in re.sub:
            for p in leading_phrases(sub):
                if p not in have:
                    have.add(p)
                    phrases.append(p)
        return phrases
    if op == OP_CONCAT:
        xs = [(BAD_WORD, BAD_WORD)]
        for sub in re.sub:
            ok = True
            for x in xs:
                if x[1] == BAD_WORD:
                    ok = False
            if ok:
                break
            ys = leading_phrases(sub)
            have = set()
            xys: list[tuple[int, int]] = []
            for x in xs:
                if x[1] != BAD_WORD:
                    if x not in have:
                        have.add(x)
                        xys.append(x)
                    continue
                for y in ys:
                    if x[0] == BAD_WORD:
                        xy = y
                    else:
                        xy = (x[0], y[0])
                    if xy not in have:
                        have.add(xy)
                        xys.append(xy)
            xs = xys
        return xs
    raise ValueError("bad op in phrases")
