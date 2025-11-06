import express from "express";
import multer from "multer";
import { exec, spawn } from "child_process";
import OpenAI from "openai";
import cors from "cors";
import path from "path";
import { Client } from "@gradio/client";
import fs from "fs";


const HF_API_URL = "https://ayushkumar3456-finalyr.hf.space/run/click";
const HF_TOKEN = "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"; // 🔑 Replace with your actual Hugging Face token


const app = express();
const upload = multer({ dest: "uploads/" });
app.use(cors());
app.use(express.json());

const client = new OpenAI({ apiKey: "" });

// helper: run python script and parse JSON
function runPython(script, inputObj) {
  return new Promise((resolve, reject) => {
    console.log("i allo");
    const py = spawn("python", [script], { stdio: ["pipe", "pipe", "pipe"] });
    //console.log(py)
    let out = "";
    let err = "";
    py.stdout.on("data", (d) => (out += d.toString()));
    py.stderr.on("data", (d) => (err += d.toString()));
    py.on("close", (code) => {
      if (code !== 0) {
        console.log("OK");
        console.log(code);
        return reject(new Error(err || `Python script exited with ${code}`));
      }
      try {
        resolve(JSON.parse(out));
      } catch (e) {
        console.log("caught");
        reject(new Error(`Invalid JSON from ${script}: ${out}`));
      }
    });
    py.stdin.write(JSON.stringify(inputObj));
    py.stdin.end();
  });
}
function sampleLogLines(filePath, n = 10) {
  const data = fs.readFileSync(filePath, "utf-8");
  const lines = data.split(/\r?\n/).filter((l) => l.trim() !== "");
  const sampled = [];

  while (sampled.length < Math.min(n, lines.length)) {
    const idx = Math.floor(Math.random() * lines.length);
    const line = lines[idx];
    if (!sampled.includes(line)) sampled.push(line);
  }

  return sampled;
}

// ---- STEP 2: build prompt for GPT ----
function buildFieldMappingPrompt(lines) {
  return `
You are a log analysis expert. Your task is to infer what each space-separated field in a log line represents.

Return a valid JSON object mapping field numbers to human-readable names, for example:
{
  "1": "epoch_timestamp",
  "2": "response_time_ms",
  "3": "client_ip",
  "4": "cache_status",
}

Guidelines:
- Use snake_case for labels.
- Each field number must appear, starting from "1".
- Detect meaning using patterns like IPs, timestamps, URLs, HTTP codes, etc.
- If unsure, use "unknown".
- Return only JSON, no extra text.

Here are 10 sample log lines:
${lines.join("\n")}
`;
}

// ---- STEP 3: call GPT-4o-mini ----
async function getFieldMapping(filePath) {
  const lines = sampleLogLines(filePath, 10);
  const prompt = buildFieldMappingPrompt(lines);

  console.log("🧾 Sample lines sent to model:\n", lines.join("\n"));
  console.log("\n⏳ Getting field mapping from GPT-4o-mini...\n");

  const completion = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    response_format: { type: "json_object" },
  });

  const output = completion.choices[0].message.content.trim();
  console.log("✅ Field Mapping JSON:\n", output);
  return output;
}

app.post("/analyze", upload.single("file"), async (req, res) => {
  const { query } = req.body;
  const filePath = path.resolve(req.file.path);
    console.log("ok");
    console.log(filePath);
    const fieldMapping = await getFieldMapping(filePath);
    console.log(fieldMapping);
  try {
    // Step 1: Ask OpenAI to generate IR
    const VALID_ACTIONS = [
      "list", "count", "top", "distinct", "unique", "unique_count",
      "group_count", "aggregate", "time_series", "rate",
      "filter", "head", "tail", "sample", "stats", "exists", "anomaly", "sort"
    ];
      console.log(query);
    const prompt = `
You are an intelligent converter that transforms natural language log-processing requests into a structured JSON called "IR" (Intermediate Representation).
You are also provided a field mapping that explains what each field number represents.

Field Mapping:${fieldMapping}
Your task:
Convert any English instruction related to log analysis, filtering, searching, counting, sorting, or aggregating into the following JSON structure:

{
  "action": "<filter | aggregate | transform | extract | etc.>",
  "file": "<log filename or null>",
  "match_pattern": "<text or regex pattern to match, or null>",
  "match_fields": "<'all' or list of field numbers or null>",
  "print_fields": [<list of field numbers to print or null>],
  "conditions": [
    {
      "id": "<unique_condition_id>",
      "field": <field number>,
      "operator": "<==, !=, >, <, >=,<=contains, matches, etc.>",
      "value": "<comparison value>",
      "type": "<string | number>"
    }
  ],
  "logic": "<logical expression connecting condition IDs (e.g., condition1 AND condition2 OR condition3)>",
  "group_by": [<field numbers to group by or null>],
  "agg_func": "<aggregation function like count, sum, avg, max, min, or null>",
  "agg_field": "<field number on which the aggregation function is applied, or null>",
  "sort_by": [<field numbers to sort by or null>],
  "sort_order": "<asc | desc | null>",
  "limit": "<number limit or null>"
}

Guidelines:
1. **Understand meaning, not words.** The user's query may not use exact IR terms.  
   Example: “restrict to 5 outputs only” → \`limit = 5\`  
   “sort ascending by column 4” → \`sort_by = [4], sort_order = "asc"\`  
   “show only entries where field 11 > 456” → add condition \`{ "field": 11, "operator": ">", "value": 456, "type": "number" }\`.

2. **Always produce valid JSON.** All keys must appear even if their values are \`null\`.

3. **Never invent information.** If a detail is missing from the user query, set that field to \`null\`.

4. **Field numbers** correspond to space-separated positions in a log line (e.g., \`$1\`, \`$2\`, etc.) if mentioned.

5. Field Mapping Usage Rules:
   - Use the provided field mapping to correctly translate semantic terms in the user query (like “IP address”, “URL”, “response time”) into their corresponding field numbers.
     Example: If field 3 = client_ip, and the user says "group by IP address", use "group_by": [3].
   - If the user says “show average response time by cache code”, and field 2 = response_time_ms, field 4 = cache_status_http_code:
     → "group_by": [4], "agg_func": "avg", "agg_field": 2.
   - If the user uses a term not exactly matching the field mapping key, infer the meaning based on semantics.  
     Example: if the user says “transfer size”, interpret it as related to “data_transferred_bytes” or whichever field in the mapping represents similar meaning.  
     The model must rely on conceptual similarity and context, not exact wording.

6. **Operators mapping examples:**
   - “equals”, “is”, “==” → "=="
   - “not equals”, “different from” → "!="
   - “greater than” → ">"
   - “less than” → "<"
   - "greater than equal to" → ">="
   - "less than equla to" → "<="
   - “contains”, “has”, “includes” → "contains"
   - “matches”, “regex match” → "matches"

7. **Actions mapping examples:**
   - “find”, “filter”, “show only” → "filter"
   - “count”, “group”, “aggregate” → "aggregate"
   - “extract”, “print”, “display” → "filter" with appropriate "print_fields".

8. **Group/aggregate logic:**
   - “count by IP” → \`"action": "aggregate", "group_by": [<field number>], "agg_func": "count"\`
   - “average of field 5 by user ID” → \`"agg_func": "avg", "group_by": [<user field>], "agg_field": 5\`
   - **\`agg_field\`** tells which field/column from the log should be used when performing the aggregation (\`agg_func\`).  
     For example, “find average of field 8 grouped by fields 10 and 13” → \`"group_by": [10, 13], "agg_func": "avg", "agg_field": 8\`.  
     If the aggregation is a count (no specific numeric field), set \`"agg_field": null\`.
   - **For all count-related phrases** (like *“count values”*, *“distinct values”*, *“unique values”*, *“how many times”*),  
     always use \`"group_by"\` for the field(s) being counted, and leave \`"agg_field": null\`.  
     Example: “count unique values in field 7” → \`"group_by": [7], "agg_func": "count", "agg_field": null\`.

9. **Condition field type:**
   - Each condition now includes a \`"type"\` attribute indicating whether the comparison value is a \`"string"\` or \`"number"\`.  
     Example: \`"type": "string"\` for \`{"field": 9, "operator": "!=", "value": "ozebtqgv"}\`.  
     Example: \`"type": "number"\` for \`{"field": 7, "operator": ">", "value": 45}\`.

10. **Condition ID and Logical Combination Rules**
   - Each condition must have a unique incremental ID in the form "condition1", "condition2", "condition3", and so on.  
   - If multiple conditions exist, combine them logically in \`"logic"\` (e.g., \`"condition1 AND condition2"\` or \`"condition1 OR condition2"\`).

11. **Time-specific rule:**  
    Whenever a condition compares **time values** (e.g., "2am", "03:00", "14:30"),  
    use \`">="\` instead of \`">"\`, and \`"<="\` instead of \`"<"\` to ensure inclusive time boundaries.

12. **Example output:**

Input:
> Show me how many times error 404 occurred per user ID in filename.log, sorted ascending.

Output:
{
  "action": "aggregate",
  "file": "filename.log",
  "match_pattern": "404",
  "match_fields": "all",
  "print_fields": [9, 1],
  "conditions": [],
  "logic": null,
  "group_by": [9],
  "agg_func": "count",
  "agg_field": null,
  "sort_by": [9],
  "sort_order": "asc",
  "limit": null
}

13. Return **only the JSON object**, no extra explanation.

---

User query: ${query}
`;

      console.log("here reaching");
    const completion = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: prompt }],
      response_format: { type: "json_object" },
    });
    console.log("Herere");
    const ir = JSON.parse(completion.choices[0].message.content);
    console.log(ir);

    let mode_gen_command;
    async function queryModel(inputJSON) {
  try {
    const HF_API_URL = "https://api-inference.huggingface.co/models/AyushKumar3456/ir-to-awk-t5";
    const HF_TOKEN = "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"; // optional if model is public
    const client = await Client.connect("AyushKumar3456/finalyrv4");
    // salesforcescodet5small-improvedv2  ir-to-command-advance Finalyrv3
    /*const initResponse = await fetch("https://ayushkumar3456-finalyr.hf.space/gradio_api/call/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data: [JSON.stringify(inputJSON)]
      }),
    });

    const initResult = await initResponse.json();
    const eventId = initResult.event_id;
    console.log("🆔 Event ID:", eventId);

    if (!eventId) {
      console.error("❌ No event_id returned — something went wrong.");
      console.log(initResult);
      return;
    }

    // 2️⃣ Step 2: Poll for result
    const resultResponse = await fetch(`https://ayushkumar3456-finalyr.hf.space/gradio_api/call/predict/${eventId}`);
    const resultData = await resultResponse.json();

    console.log("🧾 Full Result:", resultData);

    // 3️⃣ Extract generated command
    if (resultData?.data && resultData.data.length > 0) {
      console.log("\n✅ Generated Command:");
      console.log(resultData.data[0]);
      return resultData.data[0];
    } else {
      console.warn("\n⚠️ Unexpected response format:", resultData);
      return null;
    }
      */
     if ('action' in inputJSON) {
        delete inputJSON.action; 
      }
     console.log(JSON.stringify(inputJSON));
     
     const result = await client.predict("/predict", {
      ir_text: JSON.stringify(inputJSON)
    });

    console.log("✅ Model Output:");
    console.log(result.data);
    mode_gen_command=result.data[0];
    console.log(mode_gen_command);
  } catch (err) {
    console.error("❌ Error calling model:", err);
  }

}

const test_ir = {
    action: "aggregate",
    file: "filename.log",
    match_pattern: "404",
    match_fields: "all",
    print_fields: [9, 1],
    conditions: [
      { id: "condition1", field: 11, operator: ">", value: 456, type: "number" }
    ],
    logic: "condition1",
    group_by: [9],
    agg_func: "count",
    agg_field: null,
    sort_by: [9],
    sort_order: "asc",
    limit: 10
  };

  await queryModel(ir);
  //const pythonResult = await runPython("./generator.py", test_ir);
  //console.log(pythonResult);
  //mode_gen_command=pythonResult.cmd;
  // Step 2: Validate IR (Python)
  /*
    let validationErrors;
    try {
      console.log("okkkkk");
      const result = await runPython("ir_validator.py", ir);
      console.log("reeturned");
      const validationErrors = result.errors || [];
      console.log(validationErrors);
      if (validationErrors.length) {
        console.log("alll pzx")
        return res.json({ error: "Validation errors", errors: validationErrors, ir });
      }
    } catch (err) {
      console.log("fassa");
      return res.json({ error: "IR validation failed", details: err.message, ir });
    }
    
    console.log("lost")
    */
    // Step 3: Generate command (Python)
    /*
    console.log("command running 3 ")
    let command;
    try {
      const cmdResult = await runPython("command_gen.py", ir);
      command = cmdResult.command;
      console.log("command running")
      console.log(command)
    } catch (err) {
      return res.json({ error: "Command generation failed", details: err.message, ir });
    }
      */
    // Step 4: Run command
    
   console.log("I am herreeee got till step 3")
   
    function convertWindowsPathsToWsl(command) {
      return command.replace(/[A-Za-z]:\\[^\s|]*/g, (match) => {
        let wslPath = "/mnt/" + match[0].toLowerCase() + match.slice(2).replace(/\\/g, "/");
        return `'${wslPath}'`; // wrap in single quotes
      });
    }
    let command = mode_gen_command.replace(/\bfilename\.log\b/g, filePath);
    console.log(command);
    command = convertWindowsPathsToWsl(command);
    console.log(command);
    //command = `awk '{ if ((($2+0 > 200)) && (($2+0 < 300))) {key=$3; count[key]++} } END {for (k in count) {split(k,a,SUBSEP); print a[1], count[k]}}' '/mnt/c/Users/ayush/Desktop/finalyr/backend/uploads/3023b04e25131374848b44a1a9602e0b'`;
    const escapedCommand = command.replace(/"/g, '\\"').replace(/\$/g, '\\$');
    async function runWslCommand(command, res, ir) {
        try {
          const bash = spawn("wsl", ["bash", "-i"], {
            stdio: ["pipe", "pipe", "pipe"]
          });

          let stdoutData = "";
          let stderrData = "";

          // Capture output
          bash.stdout.on("data", (data) => {
            stdoutData += data.toString();
          });

          bash.stderr.on("data", (data) => {
            stderrData += data.toString();
          });
          
          bash.on("close", (code) => {
            if (code !== 0) {
              console.log("here1234");
              console.log(stderrData.trim());
              return res.json({
                error: "Command execution failed",
                command,
                details: stderrData.trim() || `Exited with code ${code}`,
              });
            }
            
            function explainCommand(command) {
                const stages = command.split("|").map((s) => s.trim());
                const explanations = [];
                let overall = "This command ";

                for (const stage of stages) {
                  if (stage.startsWith("grep")) {
                    // Handle grep
                    const m = stage.match(/grep\s+(-E\s+)?(-i\s+)?'([^']+)'/);
                    const pattern = m ? m[3] : "a pattern";
                    explanations.push(`filters lines that contain the text **"${pattern}"**`);
                  }

                  else if (stage.startsWith("awk")) {
                    // Handle AWK
                    if (stage.includes("min[")) {
                      explanations.push(
                        "groups lines based on a key (usually the first field) and finds the **minimum** numeric value for each group"
                      );
                    } else if (stage.includes("max[")) {
                      explanations.push(
                        "groups lines based on a key (usually the first field) and finds the **maximum** numeric value for each group"
                      );
                    } else if (stage.includes("count[")) {
                      explanations.push(
                        "counts how many times each key (field) appears"
                      );
                    }
                    else if (stage.includes("sum[")) {
                      explanations.push(
                        "groups lines by a key and computes the **sum** of numeric values for each group"
                      );
                    } else if (stage.includes("avg[")) {
                      explanations.push(
                        "groups lines by a key and computes the **average (mean)** numeric value for each group"
                      );
                    } else if (stage.includes("print")) {
                      const m = stage.match(/\{print\s+([^}]+)\}/);
                      explanations.push(
                        `prints fields **${m ? m[1] : "specified"}** from each line`
                      );
                    } else {
                      explanations.push("processes lines using an AWK script");
                    }
                  }

                  else if (stage.startsWith("sort")) {
                    const m = stage.match(/-k(\d+)/);
                    const fieldNum = m ? m[1] : "1";
                    const numeric = stage.includes("-n");
                    const reverse = stage.includes("-r");
                    explanations.push(
                      `sorts the results by **field ${fieldNum}** in ${numeric ? "numeric" : "lexical"} order${reverse ? " (descending)" : " (ascending)"}`
                    );
                  }

                  else if (stage.startsWith("uniq")) {
                    explanations.push(
                      stage.includes("-c")
                        ? "removes duplicate lines while counting occurrences"
                        : "removes duplicate lines"
                    );
                  }

                  else if (stage.startsWith("wc")) {
                    if (stage.includes("-l"))
                      explanations.push("counts the total number of lines in the output");
                    else explanations.push("counts words or bytes in the output");
                  }

                  else if (stage.startsWith("head")) {
                    const m = stage.match(/-n\s+(\d+)/);
                    const n = m ? m[1] : 10;
                    explanations.push(`takes only the first **${n}** lines`);
                  }

                  else if (stage.startsWith("tail")) {
                    const m = stage.match(/-n\s+(\d+)/);
                    const n = m ? m[1] : 10;
                    explanations.push(`takes only the last **${n}** lines`);
                  }

                  else {
                    explanations.push(`runs **${stage}**, a custom or unrecognized stage`);
                  }
                }

                overall += explanations.join(", then it ") + ".";

                return {
                  pipeline_stages: stages,
                  step_explanations: explanations,
                  natural_explanation: overall,
                };
              }
              async function summarizeCommandResult(original_query, command, output) {
              const systemPrompt = `You are a technical assistant that interprets the results of Linux command-line operations involving tools like awk, grep, and sed. Your goal is to summarize the result in clear, concise, and user-friendly English.

                    You will be provided with:
                    1. The **original query** (natural language request from the client).
                    2. The **command** that was generated and executed (using awk/grep/sed or a combination).
                    3. The **output** produced by running that command.

                    Your job:
                    - Understand the intent of the original query and the logic of the command.
                    - Analyze and interpret the structure of the output — not just its format (e.g., key-value pairs, counts, filters, or aggregates), but also how and why that structure arises from the given command. Identify which parts of the command (fields, operators, pipes, or functions) produce specific elements or patterns in the output
                    - Identify what kind of task the command performed: filtering, counting, aggregation (sum, avg, min, max), grouping, pattern extraction, or listing.
                    - Summarize the results **in 2-3 natural language sentences**, written for a non-technical reader.
                    - Automatically adjust your tone:
                      - If it's a count or aggregation, make it analytical (“X found, Y was highest”).
                      - If it's a filter or search, make it descriptive (“Found N matching lines with…”).
                      - If the result is empty, say that no matching data was found.
                    - Be factual and concise — do not describe command syntax, just the meaning of the result.
                    - If possible, infer column or field context based on the original query.

                    Format of your output:
                    Final Answer: <Your 2-3 line plain English summary>

                    ---

                    Examples for style guidance:

                    Example 1:
                    Original Query: count the unique values in column 2
                    Command: awk '{count[$2]++} END {for (val in count) print val, count[val]}' filename.log
                    Output:
                    192.168.1.25 1
                    192.168.1.30 4
                    Final Answer: Found 2 unique IPs in column 2 — 192.168.1.25 appeared once, and 192.168.1.30 appeared four times.

                    Example 2:
                    Original Query: find all error lines containing “Timeout”
                    Command: grep 'Timeout' filename.log
                    Output:
                    2025-10-12 ERROR Timeout connecting to DB
                    2025-10-12 ERROR Timeout in cache layer
                    Final Answer: Found 2 log entries mentioning “Timeout,” indicating connection and cache-layer timeouts.

                    Example 3:
                    Original Query: show the minimum response time for each endpoint
                    Command: awk '{if(!(min[$1]) || $2 < min[$1]) min[$1]=$2} END {for (k in min) print k, min[k]}' responses.log
                    Output:
                    /api/login 120
                    /api/user 95
                    Final Answer: The minimum response times were 120 ms for /api/login and 95 ms for /api/user.

                    Example 4:
                    Original Query: filter lines where status code > 400
                    Command: awk '$3 > 400' filename.log
                    Output:
                    2025-10-12 /api/user 404
                    2025-10-12 /api/admin 500
                    Final Answer: Found 2 log entries where the status code exceeded 400 — one for /api/user (404) and another for /api/admin (500).
                    ---

                    Now use these variables:

                    Original Query: ${original_query}
                    Command: ${command}
                    Output:
                    ${output}
                    `


                    try {
                      const response = await client.chat.completions.create({
                        model: "gpt-4o-mini",
                        messages: [
                          { role: "system", content: systemPrompt },
                        ],
                      });

                      const summary = await response.choices[0].message.content.trim();
                      console.log("✅ LLM Summary:\n", summary);
                      return summary;
                    } catch (error) {
                      console.error("❌ Error generating summary:", error);
                      return null;
                    }
              }
            let determinsitic=explainCommand(command);
            summarizeCommandResult(query, command, stdoutData.trim())
              .then(summary => {
                console.log("✅ Outside Function Summary:\n", summary);
                res.json({
                  ir,
                  command,
                  raw_output: stdoutData.trim(),
                  technical_summary: determinsitic.natural_explanation,
                  llm_summary: summary,
                });
              })
              .catch(err => {
                console.error("LLM summarization failed:", err);
                res.status(500).json({ error: "LLM summarization failed" });
              });
          });
          
          // Write the full command directly into bash (no escaping needed)
          bash.stdin.write(`${command}\n`);
          bash.stdin.end();

        } catch (err) {
          res.json({
            error: "Unexpected failure during command execution",
            details: err.message,
          });
        }
      }
      await runWslCommand(command, res, ir);
    /*
    exec(`wsl bash -c "${escapedCommand}"`, async (error, stdout, stderr) => {
      if (error) {
        
        return res.json({
          error: "Command execution failed",
          command,
          details: stderr || error.message,
        });
      }

      try {
        console.log("here");
        // (i) Deterministic Parser
        function deterministicParser(cmd, output) {
          const stages = cmd.split("|").map((s) => s.trim());
          const parsed = [];

          let technicalSummary = "Pipeline breakdown:\n";
          let finalExplanation = "";

          for (const st of stages) {
            if (st.startsWith("awk")) {
              const m = st.match(/\{print\s+([^}]+)\}/);
              parsed.push({ type: "awk", expr: m ? m[1] : "$0 (whole line)" });
              technicalSummary += `- AWK used to extract fields: ${m ? m[1] : "entire line"}\n`;
            } else if (st.startsWith("grep")) {
              const m = st.match(/grep\s+-E\s+-i\s+'([^']+)'/);
              const pattern = m ? m[1] : "unspecified";
              parsed.push({ type: "grep", pattern });
              technicalSummary += `- GREP filters lines matching pattern: "${pattern}" (case-insensitive)\n`;
              finalExplanation += `Count of log lines matching "${pattern}" `;
            } else if (st.includes("uniq")) {
              parsed.push({ type: "uniq", count: st.includes("-c") });
              technicalSummary += `- UNIQ ${st.includes("-c") ? "with counts" : "unique only"}\n`;
            } else if (st.startsWith("sort")) {
              parsed.push({ type: "sort", order: st.includes("-n") ? "numeric" : "lexical" });
              technicalSummary += `- SORT results (${st.includes("-n") ? "numeric" : "lexical"} order)\n`;
            } else if (st.startsWith("head")) {
              const m = st.match(/-n\s+(\d+)/);
              const lines = m ? parseInt(m[1]) : 10;
              parsed.push({ type: "head", lines });
              technicalSummary += `- HEAD limits output to first ${lines} lines\n`;
            } else if (st.startsWith("wc")) {
              parsed.push({ type: "wc", mode: st.includes("-l") ? "line_count" : "other" });
              technicalSummary += `- WC counts ${st.includes("-l") ? "lines" : "other"}\n`;
              finalExplanation += `in ${extractLogFile(cmd)} is ${output.trim()}.`;
            } else {
              parsed.push({ type: "other", raw: st });
              technicalSummary += `- OTHER stage: ${st}\n`;
            }
          }

          technicalSummary += "\nRaw output (truncated):\n" + output.slice(0, 500);

          return {
            stages: parsed,
            technical_summary: technicalSummary,
            deterministic_explanation: finalExplanation || "No high-level explanation generated."
          };
        }

        //const parsed = deterministicParser(command, stdout);

        // (ii) LLM Summarization
        let llmSummary = "No OpenAI API key provided.";
        if (1) {
          const completion2 = await client.chat.completions.create({
            model: "gpt-4o-mini",
            messages: [
              {
                role: "system",
                content: `You are a log analysis assistant. 
                          Summarize technical results into clear, human-friendly insights.
                          Be specific, concise, and results-oriented.
                          If counts are involved, state how many.
                          If unique/distinct values are requested, mention how many and list them (if few).
                          If filtering patterns are used, state what was matched.`
              },
              {
                role: "user",
                content: `Deterministic Explanation: ${parsed.deterministic_explanation}
                Technical Summary: ${parsed.technical_summary}

                Now summarize in 2-3 sentences, highlighting:
                1. What was being asked (pattern, distinct, count, etc.)
                2. The actual result (counts, unique values, etc.)
                3. Present it in plain English.`
              },
            ],
          });
          //llmSummary = completion2.choices[0].message.content.trim();
          //console.log(llmSummary)
        }

        res.json({
          ir,
          command,
          raw_output: stdout
        });
      } catch (parseErr) {
        res.json({
          error: "Parsing failed",
          command,
          details: parseErr.message,
          raw_output: stdout,
        });
      }
    });
    */
  } catch (err) {
    res.status(500).json({ error: "Fatal server error", details: err.message });
  }
    
});

app.listen(5000, () =>
  console.log("🚀 Server running on http://localhost:5000")
);
