# Youtube-Clippa
Youtube video gets turned into a collection of clip highlights called 'Chapters'.

## Installation

### 1. Clone the repository
```bash
git clone git@github.com:Axiom-208/Youtube-Clippa.git
cd youtube-clippa
```
### 2. Create a virtual environment
```bash
python -m venv env
source env/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set up environment variables
Create a `.env` file which will house the OpenAI API key needed for audio extraction and segmentation and put inside:
`API_KEY=your_openai_api_key_here`

# Usage
1. Start Flask application
```bash
python main.py
```
2. Open your browser and navigate to `http:127.0.0.1:5000/download`
3. Enter a Youtube URL in the input field and click "Download" (Press button/ enter once and wait for up to 30 seconds for processing to complete).
4. Application will
    - Download the video
    - Extract audio
    - Generate transcripts
    - Analyse for topic segments
    - Create clips in a newly and automatically created "chapters" folder
5. View the clips generated clips from the interface

### Requirements
- OpenAI API KEY
- Valid youtube link (Video must be under 4 minutes due to slow processing)


