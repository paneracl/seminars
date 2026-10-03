You are given a sequence of $n$ integers. Compute their sum.

## Input

The first line contains an integer $n$ ($1 \le n \le 10^5$).

The second line contains $n$ integers $a_1, a_2, \ldots, a_n$
with $|a_i| \le 2 \cdot 10^9$.

## Output

A single line with the sum

$$S = \sum_{i=1}^{n} a_i$$

## Example

| Input | Output |
|-------|--------|
| `3`<br>`1 2 3` | `6` |

## Scoring

- **Subtask 1 (30 points):** $n \le 10$ and $|a_i| \le 100$
- **Subtask 2 (70 points):** no additional constraints

> Careful: the sum may not fit in a 32-bit integer.
