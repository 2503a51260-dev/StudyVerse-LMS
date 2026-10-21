# StudyVerse LMS

StudyVerse is a Flask learning dashboard with individual student accounts, assignment tracking and attachments, and an OpenAI-powered study chat.

## Run locally

1. Install Python dependencies: `pip install -r requirements.txt`
2. Create a PostgreSQL database and set `DATABASE_URL` in `.env`.
3. Set a long, random `FLASK_SECRET_KEY` in `.env`.
4. Optionally set `OPENAI_API_KEY` to enable the AI study buddy. OpenAI API usage is billed separately by OpenAI.
5. Start the local development server with `python app.py`.

The local development server listens on the network interface by default. To open it on a phone, tablet, or other computer on the same Wi-Fi, find this computer's local IP address and open `http://<computer-lan-ip>:5000` on that device. Both devices must be on the same network and the computer firewall must allow the connection. This is local-network access only; to access the app over the internet from anywhere, deploy it publicly on Railway.

The app creates its account and assignment tables when the first account or assignment API is used. `schema.sql` contains the matching database schema and starter course catalog. New sign-ups require a PostgreSQL database. Local development retains the existing demo student login if no matching account is registered.

## Deploy on Railway

1. Push this repository to a Git provider and create a Railway project from it.
2. Add a PostgreSQL service to the project and expose its connection URL to the web service as `DATABASE_URL`.
3. Set `FLASK_SECRET_KEY` to a unique secret and set `OPENAI_API_KEY` if you want live AI chat.
4. Deploy the web service. `railway.json` configures the build, Gunicorn start command, and health check. Railway provides a generated public domain from the service settings.

Railway currently lists a Free plan with $1/month in included usage, but usage beyond that credit may be billed and the Free plan does not include custom domains. A persistent database and an always-available public app may exceed the included credit. Check the [Railway pricing page](https://railway.com/pricing) before deploying. The OpenAI API key may also incur separate charges.

## Included MVP features

- Responsive student overview, course progress cards, quick quiz, and activity analytics
- Student registration with a unique username and email; sign-in with either identifier
- Separate student and instructor registration/login roles
- Instructor-created classrooms with shareable join codes, student enrollment, due-dated assignments, reference files, and submitted-work review/download
- Private per-account task lists, completion/deletion, and attachment upload/download
- File attachments up to 15 MB (PDF, presentations, office documents, spreadsheets, images, text, and ZIP files), stored in PostgreSQL
- AI study chat through the OpenAI API, when `OPENAI_API_KEY` is configured (30 requests per account per day)

This is a learning-focused MVP, not feature parity with Google Classroom or Canvas. Instructors can create classrooms and assignments and review/download submissions, but grading/feedback, class announcements, realtime messaging, and institutional SSO are not included. Instructors choose that role at sign-up; email verification and administrator approval are not currently required.

The included course progress, quiz average, and study streak are clearly labeled starter/demo values; personal course enrollment, quiz-score history, and streak tracking are not yet persisted. Tasks and uploaded files are per-account and stored in PostgreSQL.
