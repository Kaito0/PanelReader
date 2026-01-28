requirements.txt !CUDA Toolkit: 11.8 

## 1. Create the Environment
Open your terminal or command prompt in your project folder and run:

Windows: python -m venv .venv

Linux: python3 -m venv .venv

## 2. Activate the Environment
You must "enter" the environment so your terminal knows to use the local Python version and not the system-wide one.

Windows (Command Prompt): .venv\Scripts\activate

Windows (PowerShell): .\.venv\Scripts\Activate.ps1

Linux: source .venv/bin/activate

Note: Once activated, you will usually see (.venv) appear in parentheses at the start of your command prompt line.

## 3. Install Requirements
Now that the environment is active, install your dependencies from requirements.txt file:

pip install -r requirements.txt
