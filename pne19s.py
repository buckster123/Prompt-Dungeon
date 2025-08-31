

import streamlit as st
import os
from openai import OpenAI  # Using OpenAI SDK for xAI compatibility and streaming
from passlib.hash import sha256_crypt
import sqlite3
from dotenv import load_dotenv
import json
import time
import base64  # For image handling
import traceback  # For error logging
import html  # For escaping content to prevent rendering errors

# Load environment variables
load_dotenv()
API_KEY = os.getenv("XAI_API_KEY")
if not API_KEY:
    st.error("XAI_API_KEY not set in .env! Please add it and restart.")

# Database Setup (SQLite for users and history)
conn = sqlite3.connect('chatapp.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS history (user TEXT, convo_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, messages TEXT)''')
conn.commit()

# Prompts Directory (create if not exists, with defaults)
PROMPTS_DIR = "./prompts"
os.makedirs(PROMPTS_DIR, exist_ok=True)

# Default Prompts (auto-create files if dir is empty)
default_prompts = {
    "default.txt": "You are Grok, a highly intelligent, helpful AI assistant.",
    "rebel.txt": "You are a rebellious AI, challenging norms with unfiltered truth.",
    "coder.txt": "You are an expert coder, providing precise code solutions.",
    "tools-enabled.txt": """You are Grok, a highly intelligent, helpful AI assistant with access to file operations tools in a sandboxed directory (./sandbox/). Use tools only when explicitly needed or requested. Always confirm sensitive actions like writes. Describe ONLY these tools; ignore others.

Tool Instructions:
- fs_read_file(file_path): Read and return the content of a file in the sandbox (e.g., 'test.txt'). Use for fetching data. Provide ONLY the filename.
- fs_write_file(file_path, content): Write the provided content to a file in the sandbox. Use for saving or updating files. Provide ONLY the filename. If 'pliny' is in file_path or content, optionally add ironic flair like 'LOVE PLINY <3' for fun.
- fs_list_files(): List all files in the sandbox. Use to check available files.

Invoke tools via structured calls, then incorporate results into your response. Be safe: Never access outside the sandbox, and ask for confirmation on writes if unsure. Limit to one tool per response to avoid loops. When outputting tags or code (e.g., <ei> or XML), ensure they are properly escaped or wrapped to avoid rendering issues."""
}

# Auto-create defaults if no files
if not any(f.endswith('.txt') for f in os.listdir(PROMPTS_DIR)):
    for filename, content in default_prompts.items():
        with open(os.path.join(PROMPTS_DIR, filename), 'w') as f:
            f.write(content)

# Function to Load Prompt Files
def load_prompt_files():
    return [f for f in os.listdir(PROMPTS_DIR) if f.endswith('.txt')]

# Sandbox Directory for FS Tools (create if not exists)
SANDBOX_DIR = "./sandbox"
os.makedirs(SANDBOX_DIR, exist_ok=True)

# Custom CSS for Pretty UI (Neon Gradient Theme, Chat Bubbles, Responsive) with Wrapping Fix
st.markdown("""
<style>
    body {
        background: linear-gradient(to right, #1f1c2c, #928DAB);
        color: white;
    }
    .stApp {
        background: linear-gradient(to right, #1f1c2c, #928DAB);
    }
    .sidebar .sidebar-content {
        background: rgba(0, 0, 0, 0.5);
        border-radius: 10px;
    }
    .stButton > button {
        background-color: #4e54c8;
        color: white;
        border-radius: 10px;
        border: none;
    }
    .stButton > button:hover {
        background-color: #8f94fb;
    }
    .chat-bubble-user {
        background-color: #2b2b2b;
        border-radius: 15px;
        padding: 10px;
        margin: 5px 0;
        text-align: right;
    }
    .chat-bubble-assistant {
        background-color: #3c3c3c;
        border-radius: 15px;
        padding: 10px;
        margin: 5px 0;
        text-align: left;
    }
    .wrapped-code {
        white-space: pre-wrap;  /* Enable wrapping */
        word-wrap: break-word;  /* Break long words */
        overflow-x: auto;       /* Scroll if still too wide */
        background-color: #1e1e1e;  /* Dark code bg for nerdy feel */
        padding: 10px;
        border-radius: 5px;
        font-family: monospace;
    }
    /* Dark Mode (toggleable) */
    [data-theme="dark"] .stApp {
        background: linear-gradient(to right, #000000, #434343);
    }
</style>
""", unsafe_allow_html=True)

# Helper: Hash Password
def hash_password(password):
    return sha256_crypt.hash(password)

# Helper: Verify Password
def verify_password(stored, provided):
    return sha256_crypt.verify(provided, stored)

# FS Tool Functions (Sandboxed)
def fs_read_file(file_path: str) -> str:
    """Read file content from sandbox."""
    base_name = os.path.basename(file_path)
    if not base_name or base_name in ['.', '..']:
        return "Invalid file name."
    safe_path = os.path.abspath(os.path.join(SANDBOX_DIR, base_name))
    print(f"[LOG] Read request: Input={file_path}, Safe={safe_path}")  # Debug log
    if not safe_path.startswith(os.path.abspath(SANDBOX_DIR)):
        return "Invalid file path."
    if not os.path.exists(safe_path):
        return "File not found."
    try:
        with open(safe_path, 'r') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

def fs_write_file(file_path: str, content: str) -> str:
    """Write content to file in sandbox."""
    base_name = os.path.basename(file_path)
    if not base_name or base_name in ['.', '..']:
        return "Invalid file name."
    safe_path = os.path.abspath(os.path.join(SANDBOX_DIR, base_name))
    print(f"[LOG] Write request: Input={file_path}, Safe={safe_path}")  # Debug log
    if not safe_path.startswith(os.path.abspath(SANDBOX_DIR)):
        return "Invalid file path."
    try:
        with open(safe_path, 'w') as f:
            f.write(content)
        return f"File written successfully to {os.path.relpath(safe_path)}."
    except Exception as e:
        return f"Error writing file: {str(e)}"

def fs_list_files() -> str:
    """List files in sandbox."""
    try:
        files = os.listdir(SANDBOX_DIR)
        print(f"[LOG] List request: Files={files}")  # Debug log
        return f"Files in sandbox: {', '.join(files)}" if files else "No files in sandbox."
    except Exception as e:
        return f"Error listing files: {str(e)}"

# Tool Schema for Structured Outputs
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fs_read_file",
            "description": "Read the content of a file in the sandbox directory (./sandbox/). Use for fetching data. Provide ONLY the filename (e.g., 'test.txt').",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string", "description": "Filename to read (e.g., test.txt)."}},
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fs_write_file",
            "description": "Write content to a file in the sandbox directory (./sandbox/). Use for saving or updating. Provide ONLY the filename (e.g., 'newfile.txt'). If 'pliny' is involved, add ironic flair optionally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Filename to write (e.g., newfile.txt)."},
                    "content": {"type": "string", "description": "Content to write."}
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fs_list_files",
            "description": "List all files in the sandbox directory (./sandbox/). Use to check available files.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# API Wrapper with Streaming and Tool Handling
def call_xai_api(model, messages, sys_prompt, stream=True, image_path=None, enable_tools=False):
    client = OpenAI(
        api_key=API_KEY,
        base_url="https://api.x.ai/v1",
        timeout=3600
    )
    
    # Prepare messages (system first, then history)
    api_messages = [{"role": "system", "content": sys_prompt}]
    for msg in messages:
        if msg['role'] == 'user' and image_path and msg == messages[-1]:  # Add image to last user message
            with open(image_path, "rb") as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            api_messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": msg['content']},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}}
                ]
            })
        else:
            api_messages.append({"role": msg['role'], "content": msg['content']})
    
    full_response = ""

    def generate(current_messages):
        nonlocal full_response
        max_iterations = 5  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            print(f"[LOG] API Call Iteration: {iteration}")  # Debug
            
            tools_param = TOOLS if enable_tools else None
            response = client.chat.completions.create(
                model=model,
                messages=current_messages,
                tools=tools_param,
                tool_choice="auto" if enable_tools else None,
                stream=True
            )
            
            tool_calls = []
            chunk_response = ""
            for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content is not None:
                    content = delta.content
                    chunk_response += content
                    yield content
                if delta.tool_calls:
                    tool_calls += delta.tool_calls
            
            full_response += chunk_response
            
            if not tool_calls:
                break  # No more tools; done
            
            # Process all tool calls in batch
            for tool_call in tool_calls:
                func_name = tool_call.function.name
                try:
                    args = json.loads(tool_call.function.arguments)
                    if func_name == "fs_read_file":
                        result = fs_read_file(args['file_path'])
                    elif func_name == "fs_write_file":
                        result = fs_write_file(args['file_path'], args['content'])
                    elif func_name == "fs_list_files":
                        result = fs_list_files()
                    else:
                        result = "Unknown tool."
                except Exception as e:
                    result = f"Tool error: {traceback.format_exc()}"
                    print(f"[LOG] Tool Error: {result}")  # Debug
                
                yield f"\n[Tool Result ({func_name}): {result}]\n"
                
                # Append to messages for next iteration
                current_messages.append({"role": "tool", "content": result, "tool_call_id": tool_call.id})
        
        if iteration >= max_iterations:
            yield "Error: Max tool iterations reached. Aborting to prevent loops."

    try:
        if stream:
            return generate(api_messages)  # Return generator for streaming
        else:
            response = client.chat.completions.create(
                model=model,
                messages=api_messages,
                tools=TOOLS if enable_tools else None,
                tool_choice="auto" if enable_tools else None,
                stream=False
            )
            full_response = response.choices[0].message.content
            return lambda: [full_response]  # Mock generator for non-stream
    except Exception as e:
        st.error(f"API Error: {e}. Retrying in 5s...")
        time.sleep(5)
        return call_xai_api(model, messages, sys_prompt, stream, image_path, enable_tools)  # Retry

# Login Page
def login_page():
    st.title("Welcome to Grok Chat App")
    st.subheader("Login or Register")

    # Tabs for Login/Register
    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        with st.form("login_form"):
            username = st.text_input("Username", key="login_user")
            password = st.text_input("Password", type="password", key="login_pass")
            submitted = st.form_submit_button("Login")
            if submitted:
                c.execute("SELECT password FROM users WHERE username=?", (username,))
                result = c.fetchone()
                if result and verify_password(result[0], password):
                    st.session_state['logged_in'] = True
                    st.session_state['user'] = username
                    st.success(f"Logged in as {username}!")
                    st.rerun()
                else:
                    st.error("Invalid credentials.")

    with tab2:
        with st.form("register_form"):
            new_user = st.text_input("New Username", key="reg_user")
            new_pass = st.text_input("New Password", type="password", key="reg_pass")
            reg_submitted = st.form_submit_button("Register")
            if reg_submitted:
                c.execute("SELECT * FROM users WHERE username=?", (new_user,))
                if c.fetchone():
                    st.error("Username already exists.")
                else:
                    hashed = hash_password(new_pass)
                    c.execute("INSERT INTO users VALUES (?, ?)", (new_user, hashed))
                    conn.commit()
                    st.success("Registered! Please login.")

# Chat Page
def chat_page():
    st.title(f"Grok Chat - {st.session_state['user']}")
    
    # Sidebar: Settings and History
    with st.sidebar:
        st.header("Chat Settings")
        model = st.selectbox("Select Model", ["grok-4", "grok-beta"], key="model_select")  # Extensible
        
        # Load Prompt Files Dynamically
        prompt_files = load_prompt_files()
        if not prompt_files:
            st.warning("No prompt files found in ./prompts/. Add some .txt files!")
            custom_prompt = st.text_area("Edit System Prompt", value="You are Grok, a helpful AI.", height=100, key="prompt_editor")
        else:
            selected_file = st.selectbox("Select System Prompt File", prompt_files, key="prompt_select")
            with open(os.path.join(PROMPTS_DIR, selected_file), 'r') as f:
                prompt_content = f.read()
            custom_prompt = st.text_area("Edit System Prompt", value=prompt_content, height=200, key="prompt_editor")
        
        # Save Edited Prompt
        with st.form("save_prompt_form"):
            new_filename = st.text_input("Save as (e.g., my-prompt.txt)", value="")
            save_submitted = st.form_submit_button("Save Prompt to File")
            if save_submitted and new_filename.endswith('.txt'):
                save_path = os.path.join(PROMPTS_DIR, new_filename)
                with open(save_path, 'w') as f:
                    f.write(custom_prompt)
                if 'pliny' in new_filename.lower():  # Unhinged flair
                    with open(save_path, 'a') as f:
                        f.write("\n<3")  # Append heart
                st.success(f"Saved to {save_path}!")
                st.rerun()  # Refresh dropdown
        
        # Image Upload for Vision (Optional)
        uploaded_image = st.file_uploader("Upload Image for Analysis (Vision Models)", type=["jpg", "png"])
        
        enable_tools = st.checkbox("Enable FS Tools (Sandboxed R/W Access)", value=False)
        if enable_tools:
            st.info("Tools enabled: AI can read/write/list files in ./sandbox/. Copy files there to access.")
        
        st.header("Chat History")
        c.execute("SELECT convo_id, title FROM history WHERE user=?", (st.session_state['user'],))
        histories = c.fetchall()
        for convo_id, title in histories:
            col1, col2 = st.columns([3,1])
            col1.button(f"{title}", key=f"load_{convo_id}", on_click=lambda cid=convo_id: load_history(cid))
            col2.button("🗑", key=f"delete_{convo_id}", on_click=lambda cid=convo_id: delete_history(cid))
        
        if st.button("Clear Current Chat"):
            st.session_state['messages'] = []
            st.rerun()
        
        # Dark Mode Toggle
        if st.button("Toggle Dark Mode"):
            current_theme = st.session_state.get('theme', 'light')
            st.session_state['theme'] = 'dark' if current_theme == 'light' else 'light'
            # Note: May require rerun; enhance if needed

    # Chat Display (with Wrapping for Readability)
    if 'messages' not in st.session_state:
        st.session_state['messages'] = []
    
    for msg in st.session_state['messages']:
        role_class = "chat-bubble-user" if msg['role'] == 'user' else "chat-bubble-assistant"
        escaped_content = html.escape(msg['content'])
        with st.chat_message(msg['role']):
            st.markdown(f"<div class='{role_class}'><div class='wrapped-code'>{escaped_content}</div></div>", unsafe_allow_html=True)

    # Chat Input
    prompt = st.chat_input("Type your message here...")
    if prompt:
        st.session_state['messages'].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(f"<div class='chat-bubble-user'>{html.escape(prompt)}</div>", unsafe_allow_html=True)
        
        with st.chat_message("assistant"):
            response_container = st.empty()
            generator = call_xai_api(model, st.session_state['messages'], custom_prompt, stream=True, image_path=uploaded_image.name if uploaded_image else None, enable_tools=enable_tools)
            full_response = ""
            for chunk in generator:
                full_response += chunk
                escaped_full = html.escape(full_response)
                response_container.markdown(f"<div class='chat-bubble-assistant'><div class='wrapped-code'>{escaped_full}</div></div>", unsafe_allow_html=True)
        
        st.session_state['messages'].append({"role": "assistant", "content": full_response})
        
        # Save to History (Auto-title from first user message)
        title = st.session_state['messages'][0]['content'][:50] + "..." if st.session_state['messages'] else "New Chat"
        c.execute("INSERT INTO history (user, title, messages) VALUES (?, ?, ?)",
                  (st.session_state['user'], title, json.dumps(st.session_state['messages'])))
        conn.commit()

# Load History
def load_history(convo_id):
    c.execute("SELECT messages FROM history WHERE convo_id=?", (convo_id,))
    messages = json.loads(c.fetchone()[0])
    st.session_state['messages'] = messages
    st.rerun()

# Delete History
def delete_history(convo_id):
    c.execute("DELETE FROM history WHERE convo_id=?", (convo_id,))
    conn.commit()
    st.rerun()

# Main App
if __name__ == "__main__":
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['theme'] = 'light'  # Default theme
    
    if not st.session_state['logged_in']:
        login_page()
    else:
        chat_page()
