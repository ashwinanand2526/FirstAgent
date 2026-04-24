FirstAgent: Is a ReAct-style agentic loop

Takes a natural language user query
Sends it to Gemini LLM with a system prompt that defines available tools
Parses the LLM's JSON response to decide: "use a tool" or "give a final answer"
Executes tools iteratively until the agent arrives at an answer.
