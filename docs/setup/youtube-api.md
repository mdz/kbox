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

Search has its own quota bucket, separate from the general unit pool. The
default allocations are:

- **Search requests**: 100 per day (1 unit each, from the search bucket)
- **Everything else** (video info, etc.): 10,000 units per day, 1 unit per
  request

So the practical ceiling is **~100 searches per day** by default. That is
usually enough for a party — a guest typically searches once or twice per
song they add. When it runs out, kbox logs a warning and falls back to
`yt-dlp` for the rest of the day, so search keeps working; it is just slower
(yt-dlp is rate-limited to one call every two seconds). The quota resets at
midnight Pacific Time.

Raising the limit is a request, not a purchase: use Google's [quota
extension request form](https://support.google.com/youtube/contact/yt_api_form).
Note that the two buckets are raised independently — increasing "Queries per
day" does **not** give you more searches. The row to look at (and to ask for)
is **Search Queries per day**, under **APIs & Services** > **YouTube Data API
v3** > **Quotas & System Limits** in the Cloud Console.

If you are developing a kbox feature that issues searches, assume the stock
100/day: most installs will not have an extension.

## Troubleshooting

### "API key not valid" error
- Check that you copied the key correctly
- Verify the API is enabled in your project
- Check that API restrictions allow YouTube Data API v3

### "Quota exceeded" error
- You've hit a daily limit — most likely **Search Queries per day** (default 100)
- Check which bucket is exhausted under **Quotas & System Limits** in the Cloud Console
- Search keeps working via the `yt-dlp` fallback, just more slowly — the
  quota resets at midnight Pacific Time
- If you hit this regularly, request a quota increase

### "Access denied" error
- Check IP restrictions if you set them
- Verify the API key has the correct permissions

## Security Notes

- **Never commit your API key to git**
- The key is stored in kbox's local database (`~/.kbox/kbox.db`)
- Rotate keys periodically if they're exposed
