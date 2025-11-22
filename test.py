# importing vlc module 
import vlc 

# importing pafy module 
import pafy 

# url of the video 
url = "https://www.youtube.com/watch?v=vG2PNdI8axo"

# creating pafy object of the video 
video = pafy.new(url) 

# getting best stream 
best = video.getbest() 

# creating vlc media player object 
media = vlc.MediaPlayer(best.url) 

# start playing video 
media.play() 

