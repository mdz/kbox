# Setting Up YouTube Data API v3

This guide walks you through obtaining a YouTube Data API key for kbox.

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Sign in with your Google account
3. Click the project dropdown at the top
4. Click "New Project"
5. Enter a project name (e.g., "kbox-karaoke")
6. Click "Create"

## Step 2: Enable YouTube Data API v3

1. In the Google Cloud Console, go to **APIs & Services** > **Library**
2. Search for "YouTube Data API v3"
3. Click on "YouTube Data API v3"
4. Click **Enable**

## Step 3: Create API Credentials

1. Go to **APIs & Services** > **Credentials**
2. Click **+ CREATE CREDENTIALS** at the top
3. Select **API key**
4. Your API key will be created and displayed
5. **Important**: Copy the API key immediately (you can't see it again later)

## Step 4: Restrict the API Key (Recommended)

For security, restrict your API key:

1. Click on the API key you just created (or click "Edit" if it's already open)
2. Under **API restrictions**:
   - Select "Restrict key"
   - Check "YouTube Data API v3"
   - Click "Save"
3. Under **Application restrictions** (optional but recommended):
   - Select "IP addresses" if you know your server's IP
   - Or select "None" for development/testing

## Step 5: Configure kbox

1. Start kbox: `uv run python -m kbox.main`
2. Open the web UI in your browser
3. Enter the operator PIN (default: `1234`) to unlock the config screen
4. Under the **API & Storage** section, paste your key into **YouTube API Key**
5. Save

A YouTube API key isn't required to run kbox — without one, search falls
back to `yt-dlp`. Configuring a key gets you the official Data API's search
results and quota instead of the fallback.

## Step 6: Verify Setup

1. Try searching for a song in the web UI
2. You should see search results from the YouTube Data API

## API Quota and Limits

- **Free tier**: 10,000 units per day
- **Search**: 100 units per request
- **Video info**: 1 unit per request
- This means ~100 searches per day on the free tier

For production use, you may want to:
- Monitor your quota usage in Google Cloud Console
- Set up billing alerts
- Consider upgrading if needed

## Troubleshooting

### "API key not valid" error
- Check that you copied the key correctly
- Verify the API is enabled in your project
- Check that API restrictions allow YouTube Data API v3

### "Quota exceeded" error
- You've hit the daily limit
- Wait 24 hours or increase quota in Google Cloud Console

### "Access denied" error
- Check IP restrictions if you set them
- Verify the API key has the correct permissions

## Security Notes

- **Never commit your API key to git**
- The key is stored in kbox's local database (`~/.kbox/kbox.db`)
- Rotate keys periodically if they're exposed
