import express from "express";
import multer from "multer";
import { exec, spawn } from "child_process";
import OpenAI from "openai";
import cors from "cors";
import path from "path";

const app = express();
const upload = multer({ dest: "uploads/" });
app.use(cors());
app.use(express.json());

const client = new OpenAI({  });

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

app.post("/analyze", upload.single("file"), async (req, res) => {
  const { query } = req.body;
  const filePath = path.resolve(req.file.path);

  try {
    // Step 1: Ask OpenAI to generate IR
    const VALID_ACTIONS = [
      "list", "count", "top", "distinct", "unique", "unique_count",
      "group_count", "aggregate", "time_series", "rate",
      "filter", "head", "tail", "sample", "stats", "exists", "anomaly", "sort"
    ];

    const prompt = `
      Convert this natural language log query into IR JSON.

      Schema:
      {
        "original_query": "...",
        "action": "...",   // must be one of: ${VALID_ACTIONS.join(", ")}
        "log_file": "${filePath}",
        "grep_regex": "...",
        "fields": [numbers] or "all",
        "group_by": [numbers] or null,
        "limit": number or null,
        "sort": "asc | desc | null",
        "agg": "sum | avg | ..."
      }

      Important instructions for "action":
      - Always choose one action strictly from this set: ${VALID_ACTIONS.join(", ")}.
      - Infer the action that is semantically most similar to the user’s intent.
      - Do not invent new actions.
      - If no valid synonym is clear, default to "list".

      User query: "${query}"
      `;

    const completion = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: prompt }],
      response_format: { type: "json_object" },
    });
    console.log("Herere");
    const ir = JSON.parse(completion.choices[0].message.content);
    console.log(ir);
    // Step 2: Validate IR (Python)
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
    // Step 3: Generate command (Python)
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

    // Step 4: Run command
    
    console.log("I am herreeee got till step 3")
    function convertWindowsPathsToWsl(command) {
      return command.replace(/[A-Za-z]:\\[^\s|]*/g, (match) => {
        let wslPath = "/mnt/" + match[0].toLowerCase() + match.slice(2).replace(/\\/g, "/");
        return `'${wslPath}'`; // wrap in single quotes
      });
    }
    command = convertWindowsPathsToWsl(command)
    exec(`wsl ${command}`, async (error, stdout, stderr) => {
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

        const parsed = deterministicParser(command, stdout);

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
          llmSummary = completion2.choices[0].message.content.trim();
          console.log(llmSummary)
        }

        res.json({
          ir,
          command,
          raw_output: stdout,
          technical_summary: parsed.technical_summary,
          llm_summary: llmSummary,
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
  } catch (err) {
    res.status(500).json({ error: "Fatal server error", details: err.message });
  }
});

app.listen(5000, () =>
  console.log("🚀 Server running on http://localhost:5000")
);
