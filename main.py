from flask import Flask, redirect, render_template, request, send_file, url_for
from yt_dlp import YoutubeDL
import ffmpeg
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
from threading import Thread
import firebase_admin
from firebase_admin import credentials, storage



# Set up
load_dotenv()
api_key=os.getenv("API_KEY")
cred_path=os.getenv("FIREBASE_CREDENTIALS_PATH")
bucket_name=os.getenv("FIREBASE_STORAGE_BUCKET")

app = Flask(__name__)

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
            created_clips.append(output_filename)

            

            try:
                # Use ffmpeg-python to cut the clip
                (
                    ffmpeg
                    .input(video, ss=start, to=end)
                    .output(output_filename)
                    .run()
                )
                # Uploading to firebase
                blob = bucket.blob(output_filename) # Referrence to storage location
                blob.upload_from_filename(output_filename)
                blob.make_public()
                public_url = blob.public_url
                print(public_url)
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
def process_video_in_background(url):
    """Process video in a background thread"""
    try:
        main(url)
        print(f"Finished processing video: {url}")
    except Exception as e:
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
    clip_path = trimVideo(video_path, segment_data)
    if not clip_path:
        print("Failed to generate video clips. Exiting")
        return
    
    print("Video Processing Complete")

    return


# Page Routes
@app.route('/')
def index():
    return render_template('index.jinja')
    

# Route to display form where users enter video link
@app.route('/download', methods=['POST', 'GET'])
def download():
    error = None
    video_files = []
    processing = False

    # Getting videos from firebase
    if firebase_init:
        try:
            blobs = bucket.list_blobs(prefix='chapters/')
            if blobs:
                for blob in blobs:
                    if blob.name.startswith('chapters/'):
                        blob.make_public()
                        video_files.append({
                            'name': blob.name.split('/')[-1], # Just the filename
                            'url': blob.public_url             # Direct URL to the file
                        })
        except Exception as e:
            error = f"Error retrieving videos: {e}."
    else:
        error = "Firebase storage is not configured properly."
    

    # Process form submission
    if request.method == "POST":
        url = request.form['url']
        if not url.startswith(('https://www.youtube.com/')):
           error = "Please enter a valid Youtube URL" 
        elif firebase_init: # Only process if firebase works
            # Start processing in a background thread
            thread = Thread(target=process_video_in_background, args=(url,))
            thread.daemon = True # Ensures thread closes when main program exits
            thread.start()
            return redirect(url_for('download', processing=True)) # Making a GET request
    if request.args.get('processing') == 'True':
        processing=True

    return render_template('download.jinja', video_files=video_files, error=error, processing=processing)

   
@app.route('/chapters/<path:filename>')
def chapters_static(filename):
    return send_file(os.path.join('chapters', filename))

if __name__ in "__main__":
    app.run(debug=True)