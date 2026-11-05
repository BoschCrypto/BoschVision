# kidclips: AI kids' dance-song video agent

Keeps producing short, original dance-song videos for little kids in vertical
YouTube Shorts format (1080×1920). Each run goes through these steps:

1. **Idea**: Claude writes an original concept: a character, a dance move kids can copy, song lyrics with a chorus, a 5–8 shot story, and the title, description and tags. It reads the history log so new ideas don't repeat old ones.
2. **Review**: a second Claude pass rejects anything unoriginal (existing characters or songs), unsafe for ages 2–6, or misleading, and the idea is rewritten. A name blocklist (Baby Shark, Cocomelon, Bluey…) is checked first.
3. **Song**: ElevenLabs Music sings the lyrics (or `local` makes a free placeholder beat).
4. **Shots**: any text-to-video model on Replicate (or `local` makes free placeholders).
5. **Edit**: ffmpeg crops to 9:16, joins the shots, adds sing-along lyric captions and lays the song under them.
6. **Upload** (optional): goes to YouTube as **private**, marked *made for kids* and *contains AI-generated content*.

## Quick start

```bash
pip install -e ".[kidclips]"      # ffmpeg must also be installed
kidclips make --offline           # free test run: no keys, placeholder visuals
```

Output lands in `kidclips_output/<date>-<title>/final.mp4`, along with `concept.json`.

To use real generation, fill these in `.env` (see `.env.example`):

| Setting | What for |
|---|---|
| `ANTHROPIC_API_KEY` | ideas, lyrics, review |
| `KIDCLIPS_MUSIC_PROVIDER=elevenlabs` + `ELEVENLABS_API_KEY` | sung songs |
| `KIDCLIPS_VIDEO_PROVIDER=replicate` + `REPLICATE_API_TOKEN` | animated shots |
| `KIDCLIPS_REPLICATE_VIDEO_MODEL` / `KIDCLIPS_REPLICATE_EXTRA_INPUT` | which model to use and its extra inputs. Check the model's input schema on replicate.com. |

```bash
kidclips make --theme dinosaurs                 # one video
kidclips make --notes "slower songs for toddlers, pastel colours"
kidclips youtube-auth                           # one-time Google login
kidclips loop --per-day 3 --upload              # runs until stopped (or run-kidclips.bat)
kidclips history
```

### YouTube upload setup
1. In Google Cloud Console, enable **YouTube Data API v3** and create an OAuth client of type *Desktop app*.
2. Save the JSON as `config/youtube_client_secret.json`, then run `kidclips youtube-auth` once.
3. New projects get about 10,000 API units a day, and one upload costs about 1,600 units, so the limit is about 6 uploads a day until you ask Google for more.

## Read this before you scale it up

- **Review every video before it goes public.** Uploads are private by default on purpose. AI video models get things wrong: extra limbs, a character that changes between shots, garbled faces. Kids' audiences and YouTube's reviewers are both unforgiving about this.
- **YouTube demonetizes "mass-produced or repetitive" content** (the *inauthentic content* policy in the YouTube Partner Program). Channels that upload many near-identical AI videos lose monetization. Fewer, better, more varied videos with a recurring cast you build up beats volume. That's why the loop caps videos per day and feeds past titles back in.
- **Made for kids** videos have no personalized ads, comments or notifications (COPPA), so they earn less per view. YouTube Kids picks its videos from these; you can't upload to it directly.
- **AI disclosure**: the uploader sets YouTube's synthetic-content flag. Leave it on.
- **Rights**: check that the music and video providers' terms on your plan allow commercial use. Never feed them other people's songs or characters.
