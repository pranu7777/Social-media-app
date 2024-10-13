from fastapi import FastAPI, Request, Form, HTTPException, Depends, File, UploadFile,Query,Path
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore
from datetime import datetime
from pydantic import BaseModel

app = FastAPI()

# Initialize Firestore client
firestore_db = firestore.Client()
firebase_request_adapter = requests.Request()

# Mount static files directory
app.mount('/static', StaticFiles(directory='static'), name='static')

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Define Pydantic models
class User(BaseModel):
    username: str
    email: str

class Tweet(BaseModel):
    username: str
    date: datetime
    content: str

# Function to get the current user
def get_current_user(request: Request) -> str:
    id_token = request.cookies.get("token")
    if id_token:
        try:
            user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
            user_id = user_token.get('user_id')
            user_ref = firestore_db.collection('User').document(user_id)
            user_data = user_ref.get().to_dict()
            username = user_data.get('username')

            if not username:
                raise HTTPException(status_code=401, detail="Username not found in user token")
            return username
        except ValueError as err:
            print(str(err))
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    else:
        raise HTTPException(status_code=401, detail="User not authenticated")

# Endpoint to add a tweet
@app.post("/add_tweet", response_class=HTMLResponse)
async def add_tweet(request: Request, content: str = Form(""), image: UploadFile = File(None)):
    username = get_current_user(request)
    date = datetime.now()
    tweet_data = {
        "username": username,
        "date": date,
        "content": content,
        "lowercase": content.lower(),
    }

    try:
        tweet_ref = firestore_db.collection("Tweet").document()
        tweet_ref.set(tweet_data)
        tweet_id = tweet_ref.id
        tweet_data["tweet_id"] = tweet_id
        tweet_ref.update({"tweet_id": tweet_id})

        if image:
            image_bytes = await image.read()
            image_ref = firestore_db.collection("Images").document(tweet_id)
            image_ref.set({
                "image": image_bytes,
                "tweet_id": tweet_id,
                "username": username
            })

        return HTMLResponse(content='<script>alert("tweet added"); window.location.href="/";</script>')
    except Exception as e:
        print("Error occurred while saving tweet data:", e)
        return HTMLResponse(content='<script>alert("Error occurred while tweeting!"); window.location.href="/";</script>')

# Root endpoint
@app.get("/", response_class=HTMLResponse)
async def root(request: Request, search_query: str = Query(None)):
    id_token = request.cookies.get("token")
    user_token = None
    user_email = None
    user_id = None
    username = None
    following_users=[]
    if id_token:
        try:
            user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
            user_email = user_token.get('email')
            user_id = user_token.get('user_id')
            user_ref = firestore_db.collection('User').document(user_id)
            user_data = user_ref.get().to_dict()
            if user_data and 'username' in user_data:
                username = user_data.get('username')
            else:
                return RedirectResponse('/username_add')

            following_ref = firestore_db.collection('User').where('user_id', '==', user_id)
            for user in following_ref.stream():
                following_data = user.to_dict()
                users = following_data.get("username")
                following_users = following_data.get("following_users", [])
                following_users.append(users)  # Include current user's tweets as well
            # Fetch tweets
            tweets_ref = firestore_db.collection('Tweet').where('username', 'in', following_users).order_by('date', direction=firestore.Query.DESCENDING).limit(10)
            tweets_data = [doc.to_dict() for doc in tweets_ref.stream()]

            # Filter tweets based on search query
            if search_query:
                tweets_data = [tweet for tweet in tweets_data if tweet['content'].startswith(search_query)]

        except ValueError as err:
            print(str(err))
            tweets_data = []  # Set an empty list in case of an error

    else:
        tweets_data = []  # Set an empty list if user is not authenticated

    if search_query:
        # Redirect to search endpoint if there is a search query
        return RedirectResponse(f'/search?query={search_query}')
    else:
        return templates.TemplateResponse("main.html", {'request': request, 'user_token': user_token, 'username': username, 'tweets': tweets_data})

    
# Endpoint to add username
@app.get("/username_add", response_class=HTMLResponse)
async def username_add(request: Request):
    return templates.TemplateResponse("username_add.html", {'request': request})

# Endpoint to save username
@app.post("/username_add", response_class=HTMLResponse)
async def save_username(request: Request, username: str = Form(...)):
    id_token = request.cookies.get("token")
    user_id = None
    user_email = None

    if id_token:
        try:
            user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
            user_id = user_token.get('user_id')
            user_email = user_token.get('email')

            user_ref = firestore_db.collection('User').where('username', '==', username)
            existing_users = user_ref.get()
            if existing_users:
                js_code = '''
                    <script>
                        alert("Username is already exist!.");
                        window.history.back();
                    </script>
                '''
                return HTMLResponse(content=js_code)

            user_data = {
                'user_id': user_id,
                'email': user_email,
                'username': username
            }
            firestore_db.collection('User').document(user_id).set(user_data, merge=True)

            js_code = '''
                <script>
                    alert("Username  added!");
                    window.location.href = "/";
                </script>
            '''
            return HTMLResponse(content=js_code)

        except ValueError as err:
            print(str(err))

    return RedirectResponse('/')

@app.post("/delete_tweet/{tweet_id}", response_class=HTMLResponse)
async def delete_tweet(tweet_id: str, request: Request, current_user: str = Depends(get_current_user)):
    try:
        # Check if the tweet exists
        tweet_ref = firestore_db.collection("Tweet").document(tweet_id)
        tweet_data = tweet_ref.get().to_dict()
        if not tweet_data:
            raise HTTPException(status_code=404, detail="Tweet not found")

        # Check if the authenticated user is the owner of the tweet
        username = tweet_data.get("username")
        if current_user != username:
            raise HTTPException(status_code=403, detail="You are not authorized to delete this tweet")

        # Delete the tweet
        tweet_ref.delete()

        return HTMLResponse(content='<script>alert("Tweet deleted"); window.location.href="/";</script>')
    except Exception as e:
        print("Error occurred while deleting tweet:", e)
        return HTMLResponse(content='<script>alert("Error occurred while deleting tweet!"); window.location.href="/";</script>')
    
@app.get("/search", response_class=HTMLResponse)
async def search_username(request: Request, query: str = Query(...)):
    try:
        # Perform a Firestore query to search for usernames
        user_ref = firestore_db.collection('User').where('username', '>=', query).where('username', '<', query + u'\uf8ff')
        search_results = [doc.to_dict() for doc in user_ref.stream()]
    except Exception as e:
        print("Error occurred during username search:", e)
        search_results = []

    return templates.TemplateResponse("search_results.html", {'request': request, 'search_query': query, 'search_results': search_results})

@app.get("/search_tweets", response_class=HTMLResponse)
async def search_tweets(request: Request, query: str = Query(...)):
    try:
        # Perform a Firestore query to search for tweets
        tweets_ref = firestore_db.collection('Tweet').where('content', '>=', query).where('content', '<', query + u'\uf8ff')
        search_results = [doc.to_dict() for doc in tweets_ref.stream()]
    except Exception as e:
        print("Error occurred during tweet search:", e)
        search_results = []

    return templates.TemplateResponse("search_results_tweets.html", {'request': request, 'search_query': query, 'search_results': search_results})

# Endpoint to edit a tweet
@app.post("/edit_tweet/{tweet_id}", response_class=HTMLResponse)
async def edit_tweet(tweet_id: str, request: Request, edit_content: str = Form(""), image: UploadFile = File(None)):
    username = get_current_user(request)
    date = datetime.now()
    tweet_data = {
        "username": username,
        "date": date,
        "content": edit_content,
        "lowercase": edit_content.lower(),
    }

    try:
        tweet_ref = firestore_db.collection("Tweet").document(tweet_id)
        tweet_ref.update(tweet_data)  # Update the existing tweet with new content

        if image:
            # Update image if provided
            image_bytes = await image.read()
            image_ref = firestore_db.collection("Images").document(tweet_id)
            image_ref.set({
                "image": image_bytes,
                "tweet_id": tweet_id,
                "username": username
            })

        return HTMLResponse(content='<script>alert("Tweet updated"); window.location.href="/";</script>')
    except Exception as e:
        print("Error occurred while updating tweet data:", e)
        return HTMLResponse(content='<script>alert("Error occurred while updating tweet!"); window.location.href="/";</script>')
    

@app.get("/detialsofUser/{username}", response_class=HTMLResponse)
async def user_profile(request: Request, username: str = Path(...), current_user: str = Depends(get_current_user)):
    # Retrieve user data from Firestore
    user_ref = firestore_db.collection('User').where('username', '==', username)
    user_docs = user_ref.get()
    user_data = None
    for doc in user_docs:
        user_data = doc.to_dict()
        break  # Assuming there's only one user with the given username

    if not user_data:
        return HTMLResponse(content=f"<h1>User '{username}' not found</h1>")

    # Retrieve user's last 10 tweets from Firestore
    tweets_ref = firestore_db.collection('Tweet').where('username', '==', username).order_by('date', direction=firestore.Query.DESCENDING).limit(10)
    tweets_data = [tweet.to_dict() for tweet in tweets_ref.stream()]
    
    # Retrieve current user data from Firestore
    current_user_ref = firestore_db.collection('User').where('username', '==', current_user)
    current_user_data = None
    for currentuser in current_user_ref.stream():
        current_user_data = currentuser.to_dict()

    return templates.TemplateResponse("detialsofUser.html", {'request': request, 'user_data': user_data, 'tweets': tweets_data, 'current_user_data': current_user_data})


   
@app.post("/follow", response_class=HTMLResponse)
async def follow(request: Request, current_user: str = Depends(get_current_user)):
    # Check if the current user is already following the user
    form = await request.form()
    followed_user = form["username"]
    current_user_ref = firestore_db.collection('User').where('username', '==', current_user)
    current_user_data = None
    for currentuser in current_user_ref.stream():
        current_user_data = currentuser.to_dict()
        following_list = current_user_data.get("following_users", [])
        if followed_user in following_list:
            return HTMLResponse(content='<script>alert("You are already following this user!"); window.location.href="/";</script>')

    # If not already following, add the followed user's username to the current user's following list
    for currentuser in current_user_ref.stream():
        currentuser.reference.update({"following_users": firestore.ArrayUnion([followed_user])})
    # Redirect back to the profile page of the followed user
    return HTMLResponse(content='<script>alert("Followed!"); window.location.href="/";</script>')


@app.post("/unfollow", response_class=HTMLResponse)
async def unfollow(request: Request, current_user: str = Depends(get_current_user)):
    form = await request.form()
    followed_user = form["username"]
    # Check if the current user is already following the user
    current_user_ref = firestore_db.collection('User').where('username', '==', current_user)
    current_user_data = None
    for currentuser in current_user_ref.stream():
        current_user_data = currentuser.to_dict()
        following_list = current_user_data.get("following_users", [])
        if followed_user in following_list:
            following_list.remove(followed_user)
            currentuser.reference.update({"following_users":following_list})

    return HTMLResponse(content='<script>alert("Unfollowed!"); window.location.href="/";</script>')