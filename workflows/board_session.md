# Board Session Workflow

## Objective
Get multi-perspective advice from 7 GOAT-level billionaire personas on any question, problem, or decision. Each responds in character based on Hariv's personal profile.

## The Board
| Advisor | Seat |
|---|---|
| Elon Musk | First principles, speed, moonshots |
| Jeff Bezos | Customer obsession, long game, operations |
| Warren Buffett | Capital, moats, patience |
| Steve Jobs | Product, marketing, simplicity |
| Charlie Munger | Mental models, avoiding stupidity, inversion |
| Peter Thiel | Contrarian thinking, monopoly strategy |
| Ray Dalio | Systems, principles, radical transparency |

## Required Inputs
- Your question or situation (be specific — the more specific, the better the advice)
- Profile loaded at `agent/profile.md` (already set up)

## How to Run
```bash
cd /Users/harivkannan/Desktop/DOLLA/agent
python tools/board_session.py "Your question here"
```

## Example Questions That Work Well
- "Should I wait for Shakti Electricals to implement my system before pursuing other clients?"
- "I have 18 days left to hit my July 15 goal and I'm nowhere near it. What do I do?"
- "Cold outreach isn't working. My warm network is my only pipeline. How do I fix this?"
- "I want to move to New York in 18 months. What's the fastest legitimate path?"
- "I'm spending 9 hours a day at a job I want to quit. How do I accelerate the exit?"

## Output Structure
1. Each board member responds in character (~250 words each)
2. Board synthesis: what they agree on, the key disagreement, one concrete next move

## When to Use This
- Before making a major business decision
- When you feel stuck or are going in circles
- When you want your plan torn apart before you commit to it
- When you need a next move and can't see clearly

## Edge Cases
- If responses feel generic, make the question more specific — include numbers, timelines, actual names
- The board responds based on profile.md — keep that file current as your situation changes
- For emotional/personal decisions, use a different tool (this board is business-only)

## Setup (First Time)
```bash
pip install anthropic python-dotenv
# Add your ANTHROPIC_API_KEY to agent/.env
```
