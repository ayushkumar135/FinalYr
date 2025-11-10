import { useState, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import "./ChatApp.css";

export default function ChatApp() {
  const [messages, setMessages] = useState([
    { role: "assistant", content: "Hello 👋 Upload a file and ask me something." },
  ]);
  const [input, setInput] = useState("");
  const [file, setFile] = useState(null);
  const [nestedMode, setNestedMode] = useState(false);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim()) return;
    if (!file) {
      alert("Please upload a .txt file first!");
      return;
    }

    const newMessage = { role: "user", content: input.trim() };
    setMessages((prev) => [...prev, newMessage]);
    setInput("");

    try {
      const formData = new FormData();
      formData.append("query", input.trim());
      formData.append("file", file);
      formData.append("nested", nestedMode ? "true" : "false");


      const res = await fetch("http://localhost:5000/analyze", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `📝 IR:\n${JSON.stringify(data.ir, null, 2)}` },
        { role: "assistant", content: `📜 Command:\n${data.command}` },
        { role: "assistant", content: `📂 Raw Output:\n${data.raw_output}` },
        { role: "assistant", content: `🤖 Technical Summary:\n${data.technical_summary}` },
        { role: "assistant", content: `✨ Human Summary:\n${data.llm_summary}` },      
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `❌ Error: ${err.message}` },
      ]);
    }
  };

  return (
    <div className="chat-container">
      <header className="chat-header">ShellSense</header>

      <div className="chat-messages">
        {messages.map((msg, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className={`chat-bubble ${msg.role}`}
          >
            {msg.content}
          </motion.div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input">
        {/* File input */}
        <input
          type="file"
          accept=".txt"
          onChange={(e) => setFile(e.target.files[0])}
        />
         <button
          className="nested-toggle"
          onClick={() => setNestedMode((prev) => !prev)}
        >
          {nestedMode ? "Nested Mode ✅" : "Nested Mode OFF"}
        </button>
        {/* Text input */}
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder="Type your query..."
        />

        {/* Send button */}
        <button onClick={sendMessage}>Send</button>
      </div>
    </div>
  );
}
