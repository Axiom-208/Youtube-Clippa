from flask import Flask, request, jsonify
from yt_dlp import YoutubeDL
import ffmpeg
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import uuid
from datetime import datetime
from threading import Thread
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, storage



# Set up
load_dotenv()
api_key=os.getenv("API_KEY")
cred_path=os.getenv("FIREBASE_CREDENTIALS_PATH")
bucket_name=os.getenv("FIREBASE_STORAGE_BUCKET")

app = Flask(__name__)
CORS(app)
firebase_init = False
try:

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'storageBucket': bucket_name
    })
    bucket = storage.bucket()
    firebase_init = True
except Exception as e:
    print(f"Firebase Initialisation error: {e}")
    firebase_init = False

# In-memory tracking for different jobs'
jobs = {}

# Functions
def downloadVideo(url):
    """Given a url to a youtube video, it locally downloads the video"""
    video_filename = 'output.mp4'
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': video_filename,  # Output filename saved as output.mp4
        'merge_output_format': 'mp4',  # Force merged output to be MP4
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            print("Video Downloaded")
            return video_filename
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def getAudio(video_path):
    """Given a video filepath as it's input, it converts the video to audio"""
    audio_filename = 'audio.mp3'
    try:
        (
            ffmpeg.input(video_path)
            .output(audio_filename, format='mp3', acodec='libmp3lame', ab='128k', vn=None)
            .run(overwrite_output=True)
        )


        print("Successfully converted to audio.mp3")
        return audio_filename
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


# Converting the seconds to time so it is human readable (display purposes)
def format_time(seconds):
    """Converts time from ss(seconds) to m:ss (minutes:seconds)"""
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes}:{seconds}"


def generateTranscripts(audio_file):
    """Given an audio file, it uses OpenAi's whisper model to generate transcripts and stores it in a .txt file locally."""
    transcript_filename = 'transcripts.txt'

    try:
        # Passing audio through whisper
        client = OpenAI(api_key=api_key)
        with open(audio_file, 'rb') as f:
            transcription = client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        # Storing them locally in a file
        with open(transcript_filename, 'w') as file:
            for segment in transcription.segments:
                start_time = segment.start
                end_time = segment.end
                text = segment.text
                file.write(f"Start{format_time(start_time)}, End: {format_time(end_time)}, Text: {text}\n")

        print("Transcript file generated as {transcript_filename}")




        # Return raw transcript content for next function
        full_transcript = " ".join([segment.text for segment in transcription.segments])
        return transcript_filename, full_transcript
    except Exception as e:
        print(f"An occurred during transcription: {e}")
        return None, None


def transcriptHighlights(transcript):
    """Given a transcript, a model from OpenAI will analyse it and return back the highlights in JSON"""
    json_filename = 'topic_segments.json'

    try:
        client = OpenAI(api_key=api_key)
        # Have GPT model parse through the transcripts
        prompt = f"""
        Analyse this video transcript and identify distinct topic segments that would work well as 
        standalone clips for platforms like TikTok. For each segment, provide:
        1. A descriptive title
        2. The start time
        3. The end time

        Format your response as JSON with the following structure:
        {{
            "topics": [
                {{
                    "title": "Topic Title",
                    "start_time": "m:ss",
                    "end_time": "m:ss"
                }}
            ]
        }}

        Transcript:
        {transcript}
        """

        # API call to analyse the transcripts
        topic_response = client.chat.completions.create(
            model="gpt-4-turbo",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are an expert at identifying coherent topic segments in educational videos."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        # Convert JSON response into python dictionary so we can use dict notation to parse through the segments
        topic_segments = json.loads(topic_response.choices[0].message.content)

        with open('topic_segments.json', 'w') as f:
            json.dump(topic_segments, f, indent=2)

        print("\nTOpic segments saved to 'topic_segments.json'")
        return json_filename, topic_segments
    except Exception as e:
        print(f"An error occurred during highlight analyse: {e}")
        return None, None
    
# Converts formatted time back into seconds for trimming purposes
def time_to_seconds(time_str):
    parts = time_str.split(':')
    if len(parts) == 2:
        # e.g, 2 mins 30 secs is 2 * 60 = 120 seconds + 30 seconds so 120 + 30 = 150 seconds
        return int(parts[0]) * 60 + int(parts[1])
    else: # Just seconds in that case return as is
        return int(parts[0])


def trimVideo(video, segments):
    """Given the video file to trim and the segments in JSON format, it returns clips matching to the segment lengths and saves it a "chapters" folder in the current directory"""
    # Create output directory which will house all the clips
    output_dir = "chapters"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    created_clips = []
    try:
        # Cut the video into segments based on topics
        for i, topic in enumerate(segments['topics']):
            # Get start and end time in seconds
            start = time_to_seconds(topic['start_time'])
            end = time_to_seconds(topic['end_time'])

            # Clean title to use as filename (removing special characters)
            clean_title = ''.join(c if c.isalnum() or c in [' ', '_'] else '_' for c in topic['title'])
            clean_title = clean_title.replace(' ', '_')

            # Output filename
            output_filename = f"{output_dir}/clip_{i+1}_{clean_title}.mp4"
            

            
            try:
                # Use ffmpeg-python to cut the clip
                (
                    ffmpeg
                    .input(video, ss=start, to=end)
                    .output(output_filename, c='copy')
                    .run(capture_stdout=True, capture_stderr=True)
                )
                # Uploading to firebase
                blob = bucket.blob(output_filename) # Referrence to storage location
                blob.upload_from_filename(output_filename)
                blob.make_public()
                public_url = blob.public_url
                # Storing the public URLs of the clips
                created_clips.append({
                    'title': topic['title'],
                    'url': public_url
                })
                # After upload is done we remove the videos stored locally
                os.remove(output_filename)
            except Exception as e:
                print(f"Error creating clip {i+1}: {e}")


        os.rmdir(output_dir) # Removes directory
        print("All clips have been created")
        return created_clips
    except Exception as e:
        print(f"an Error occurred during video trimming: {e}")
        return None
    


#  Runs in a background thread
def process_video_in_background(url, job_id):
    """Process video in a background thread"""

    # Initilialising job status
    jobs[job_id] = {
        'status': 'processing',
        'created_at': datetime.now().isoformat(),
        'clips': []
    }

    try:
        clips = main(url)

        # Update job status and store clip info
        if clips:
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['clips'] = clips
        else:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = 'Failed to generate video'

        print(f"Finished processing video: {url}")
    except Exception as e:

        # Update job status if failed
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] =  str(e)
        print(f"Error in background processing {e}")
    finally:
        # Clean up temp files
        temp_files = ['output.mp4', 'audio.mp3', 'transcripts.txt', 'topic_segments.json']
        for file in temp_files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as e:
                    print(f"Error removing {file}: {e}")
    return

def main(url, job_dir=None):
    """Main function to coordinate entire workflow"""
    print("Starting video processing workflow")
    # 1: Download video
    video_path = downloadVideo(url)
    if not video_path:
        return 
    # 2: Extract audio
    audio_path = getAudio(video_path)
    if not audio_path:
        return 
    
    # 3: Generate transcript from audio
    transcript_path, transcript_content = generateTranscripts(audio_path)
    if not transcript_path or not transcript_content:
        print("Failed to generate transcripts. Exiting")
        return
    
    # 4: Analyse transcript to find topic segments
    segments_path, segment_data = transcriptHighlights(transcript_content)
    if not segments_path or not segment_data:
        print("Failed to analyse transcript highlighting . Exiting")
        return
    
    # 5: Trim the video into clips based on topic segments
    clips = trimVideo(video_path, segment_data)
    if not clips:
        print("Failed to generate video clips. Exiting")
        return
    
    print("Video Processing Complete")

    return clips



@app.route('/api/clips/create', methods=['POST'])
def create_clips():
    """API endpoint to create clips from a youtube URL"""
    try:
        # Get data from request
        data = request.json
        youtube_url = data.get('url')
        # user_id = data.get('user_id', 'anon')

        if not youtube_url:
            return jsonify({
                'success': False,
                'error': 'Missing Youtube URL'
            }), 400
        if not youtube_url.startswith(('https://www.youtube.com/', 'https://youtu.be/')):
            return jsonify({
                'success': False,
                'error': 'Invalid Youtube URL'
            }), 400 # Bad Request

        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Start processing in a background thread
        thread = Thread(target=process_video_in_background, args=(youtube_url, job_id))
        thread.daemon = True # Ensures thread closes when main program exits
        thread.start()

        return jsonify({
            'success': True,
            'job_id': job_id,
            'message': 'Processing started'
        }), 202 # Accepted but processing
    
    except Exception as e:
        return jsonify ({
            'success': False,
            'error':str(e)
        }), 500 # Internal Server error

@app.route('/api/clips/<job_id>', methods=['GET'])
def get_clips_status(job_id):
    """API endpoint to check status of a job generating clips"""
    if job_id not in jobs:
        return jsonify({
            'success': False,
            'error': 'Job not found'
        }), 404
    
    job_data = jobs[job_id]
    return jsonify({
        'success':True,
        'job_id': job_id,
        'status': job_data['status'],
        'created_at': job_data['created_at'],
        'clips': job_data.get('clips', []) # incase the clips haven't been created
    })
if __name__ in "__main__":
    app.run(debug=True)
