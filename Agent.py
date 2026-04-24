"""
Session 3 - Demo 10: The Full Agent
This is the complete agent with the agentic loop.
Everything from the session comes together here.

Before running:
  pip install google-genai python-dotenv
  Create a .env file next to this script with:
    GEMINI_API_KEY=your-key-here
    GEMINI_MODEL=gemini-2.5-flash-lite
"""
from google import genai
import json
import re
import math
import os
import time
import random
from dotenv import load_dotenv

# ============================================================
# Configuration
# ============================================================
load_dotenv()   # reads .env in the current directory

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")    #gemini-3.1-flash-lite-preview
THROTTLE_SECONDS = 10  # Wait before each LLM call to stay under free-tier RPM limits

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set. Create a .env file with GEMINI_API_KEY=...")

client = genai.Client(api_key=GEMINI_API_KEY)


def call_llm(prompt: str) -> str:
    """Send a prompt to Gemini and return the text response.

    Sleeps for THROTTLE_SECONDS before each call to stay under the free-tier
    rate limit (Gemini 3.1 Flash Lite: 15 RPM, 500 RPD).
    """
    #print(f"  [waiting {THROTTLE_SECONDS}s to respect rate limits...]", flush=True)
    #time.sleep(THROTTLE_SECONDS)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text


# ============================================================
# System Prompt — This is what turns an LLM into an agent
# ============================================================
system_prompt = """You are a helpful AI agent that can use tools to answer questions accurately.

You have access to the following tools:

1. check_breakdown_or_breakout(low: int, high: int) -> str
    Checks the stock price of given script with last days low and high values 
    Examples: check_breakdown_or_breakout(90, 100)

2. calculate(expression: str) -> str
   Evaluate a mathematical expression using Python syntax.
   Examples: calculate("2**10"), calculate("math.sqrt(144)"), calculate("sum(math.exp(x) for x in [1,1,2,3,5,8])")

3. search_notes(query: str) -> str
   Search through user's notes for relevant information.
   Examples: search_notes("meeting"), search_notes("project ideas")

You must respond in ONE of these two JSON formats:

If you need to use a tool:
{"tool_name": "<name>", "tool_arguments": {"<arg_name>": "<value>"}}

If you have the final answer:
{"answer": "<your final answer>"}

IMPORTANT RULES:
- Respond with ONLY the JSON. No other text. No markdown code fences.
- Use tools when you need real data or precise calculations.
- After receiving a tool result, either use another tool or provide your final answer.
- For complex calculations, break them down into steps if needed.
- ALWAYS use the calculate tool for math — do NOT try to compute in your head.
"""


# ============================================================
# Tools — The functions the agent can call
# ============================================================

def check_breakdown_or_breakout(low: int, high: int) -> str:
    """
    Generate a random integer between (low - 10) and (high + 10).

    Returns:
        'breakout'  -> if number > high
        'breakdown' -> if number < low
        'in range'  -> if low <= number <= high
    """
    num = random.randint(low - 10, high + 10)

    if num > high:
        return "breakout"
    elif num < low:
        return "breakdown"
    else:
        return "in range"


def calculate(expression: str) -> str:
    """Evaluate a mathematical expression safely"""
    try:
        # Allow math functions in the expression
        allowed = {
            "math": math,
            "abs": abs,
            "round": round,
            "pow": pow,
            "sum": sum,
            "min": min,
            "max": max,
            "range": range,
            "list": list,
        }
        result = eval(expression, {"__builtins__": {}}, allowed)
        return json.dumps({"result": str(result)})
    except Exception as e:
        return json.dumps({"error": f"Calculation failed: {str(e)}"})


def search_notes(query: str) -> str:
    """Search through user's notes (simulated)"""
    #breakpoint()
    notes = [
        {"title":"Apple yesterday range", "content": "low 150 high 160"},
        {"title":"google yesterday range", "content": "low 97 high 112"},
        {"title":"Amazon weekly range", "content": "low 50 high 70"},
    ]
    #results = [
    #    n for n in notes
    #    if query.lower() in n["title"].lower() or query.lower() in n["content"].lower()
    #]
    query_words = query.lower().split()

    results = [
        n for n in notes
        if all(
            word in (n["title"] + " " + n["content"]).lower()
            for word in query_words
        )
    ]
    if results:
        return json.dumps({"results": results})
    return json.dumps({"results": "No notes found matching your query"})


# Tool registry — maps tool names to functions
tools = {
    "check_breakdown_or_breakout":check_breakdown_or_breakout,
    "calculate": calculate,
    "search_notes": search_notes,
}


# ============================================================
# Response Parser — Handles messy LLM output
# ============================================================

def parse_llm_response(text: str) -> dict:
    """Parse the LLM's response, handling common formatting issues"""
    text = text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (opening fence)
        lines = lines[1:]
        # Remove last line if it's a closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        # Remove language identifier
        if text.startswith("json"):
            text = text[4:].strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse LLM response: {text[:200]}")


# ============================================================
# The Agent Loop — This is where the magic happens
# ============================================================

def run_agent(user_query: str, max_iterations: int = 5, verbose: bool = True):
    """
    Run the agent loop:
    User query → LLM → [Tool call → Result → LLM]* → Final answer

    This is THE pattern. Everything else in this course builds on this loop.
    """
    #breakpoint()
    if verbose:
        print(f"\n{'='*60}")
        print(f"  User: {user_query}")
        print(f"{'='*60}")

    # Conversation history — this is the agent's "working memory"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    for iteration in range(max_iterations):
        if verbose:
            print(f"\n--- Iteration {iteration + 1} ---")

        # Build prompt from message history
        # Each iteration, the LLM sees EVERYTHING that happened before
        prompt = ""
        for msg in messages:
            if msg["role"] == "system":
                prompt += msg["content"] + "\n\n"
            elif msg["role"] == "user":
                prompt += f"User: {msg['content']}\n\n"
            elif msg["role"] == "assistant":
                prompt += f"Assistant: {msg['content']}\n\n"
            elif msg["role"] == "tool":
                prompt += f"Tool Result: {msg['content']}\n\n"

        # Call the LLM
        response_text = call_llm(prompt)
        if verbose:
            print(f"LLM: {response_text.strip()}")

        # Parse the response
        try:
            parsed = parse_llm_response(response_text)
        except (ValueError, json.JSONDecodeError) as e:
            if verbose:
                print(f"Parse error: {e}")
                print("Asking LLM to retry...")
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": "Please respond with valid JSON only. No markdown, no extra text."})
            continue

        # Check if it's a final answer
        if "answer" in parsed:
            if verbose:
                print(f"\n{'='*60}")
                print(f"  Agent Answer: {parsed['answer']}")
                print(f"{'='*60}")
            return parsed["answer"]

        # It's a tool call — execute it
        if "tool_name" in parsed:
            tool_name = parsed["tool_name"]
            tool_args = parsed.get("tool_arguments", {})

            if verbose:
                print(f"→ Calling tool: {tool_name}({tool_args})")

            # Check if tool exists
            if tool_name not in tools:
                error_msg = json.dumps({"error": f"Unknown tool: {tool_name}. Available: {list(tools.keys())}"})
                if verbose:
                    print(f"→ Error: {error_msg}")
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "tool", "content": error_msg})
                continue

            # Execute the tool
            tool_result = tools[tool_name](**tool_args)
            if verbose:
                print(f"→ Result: {tool_result}")

            # Add to conversation history — the LLM will see this next iteration
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "tool", "content": tool_result})

    print("\nMax iterations reached. Agent could not complete the task.")

    # Print full conversation for debugging
    if verbose:
        print(f"\n{'='*60}")
        print("Full conversation history:")
        print(f"{'='*60}")
        for i, msg in enumerate(messages):
            print(f"[{i}] {msg['role']}: {msg['content'][:100]}...")

    return None


# ============================================================
# Run the agent!
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  SESSION 3: YOUR FIRST AI AGENT")
    print("  Let's see the agent loop in action!")
    print("=" * 60)

    # Test 0: Check stock prices
    print("\n\n>>> TEST 0: Check stock prices")
    #run_agent("Is the stock prices of GOOGLE still in the range of 95 & 100 ? ")
    run_agent("what is the stock prices of GOOGLE, still in the yesterdays range? if yes, tell me a joke! ")
   