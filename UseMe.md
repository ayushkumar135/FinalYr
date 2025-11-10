
# ShellSense — UseMe Guide

## ✅ Overview
ShellSense is an advanced natural‑language–driven log analysis tool.  
You upload a `.tsv`, `.txt` or `.csv` file, type your query in natural language, and ShellSense converts it into an IR → AWK/Sed command → executes it → returns results with LLM summarisation.

It supports:
- ✅ File upload  
- ✅ Nested Mode (run on previous output)  
- ✅ Natural Language Query  
- ✅ Real-time output display  
- ✅ Field mapping + IR generation via GPT‑4o‑mini  
- ✅ Command execution in WSL  

---

## 🚀 Running the Project

### 1️⃣ **Frontend**
```bash
cd ./frontend/
npm install
npm run dev
```

### 2️⃣ **Backend**
```bash
cd ./backend/
npm install
nodemon index.js
```

---

## 📤 File Upload
You can upload:
- `.tsv`
- `.txt`
- `.csv`

More file types may be added later.

---

## 🔁 Nested Mode
- **Nested Mode ON** → Your query runs on the **previous output** instead of the uploaded file.
- **Nested Mode OFF** → Your query runs on the **original uploaded file**.

This is useful for chaining queries like:
- First: “filter all rows where bytes > 5000”
- Then: “count unique IPs” (runs on filtered results)

---

## 🧠 How the System Works Internally
1. User query → sent to **GPT‑4o-mini**  
2. GPT‑4o-mini converts it → **IR (Intermediate Representation)**  
3. IR passed to a Python script → **builds AWK/Sed pipeline**  
4. Command executed in **WSL**  
5. Output returned → GPT‑4o-mini summarises it  
6. Results + explanation shown in chat window  

⚠️ Since GPT‑4o-mini is used, the free quota is **100k tokens per Gmail account**.  
After it's exhausted:
- Create new Gmail account
- Go to https://platform.openai.com/api-keys  
- Generate new API key  
- Replace line 19 in `/backend/index.js`:

```js
const client = new OpenAI({ apiKey: "YOUR_NEW_API_KEY" });
```

## ✅ Option 2: Add Balance to Existing Account (Recommended)

- Add **$5** to your OpenAI wallet.
- GPT-4o-mini then costs only **$0.15 per million tokens**.
- This gives you extremely cheap usage without switching accounts.

---

## 💬 UI Guide
### ✅ **Type Query**
Write your natural language request here  
(e.g., “count unique IPs where field 4 > 420”).

### ✅ **Nested Mode**
- Toggle ON/OFF  
- Shows green when active

### ✅ **Send**
Sends:
- File  
- Nested setting  
- Query  
All together to the backend.

### ✅ **Output Panel**
Shows:
- Generated IR  
- AWK command  
- Raw output  
- Technical explanation  
- Human-friendly summary  

---

## ✅ Notes
- Ensure WSL is installed & functional  
- Backend logs will show progress (helpful if internet is slow)  
- Execution typically takes **~15-20 seconds** depending on internet speed

---

## ✅ Enjoy using ShellSense 🚀  
Feel free to contribute, fork, or enhance the project!
