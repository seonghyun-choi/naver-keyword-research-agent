🔎 Naver Keyword Research Agent

An AI-assisted keyword research system designed to discover trending, high-demand, and relatively low-competition topics for Naver Blog.

Instead of manually browsing news and guessing which topics might perform well, this project builds a data-driven pipeline that collects recent content, generates candidate keywords, measures keyword demand and content supply, and identifies potential blogging opportunities.

Project Status: Active Development
Primary Use Case: Naver Blog keyword discovery and content research
Development Approach: AI-assisted development with iterative testing and validation

⸻

🎯 Project Goal

The core question behind this project is:

“Which topics are people actively searching for, but have relatively little competing blog content?”

The system is designed to identify these opportunities by combining:

* Recent news and trend signals
* Candidate keyword generation
* Search-demand indicators
* Naver Blog content volume
* Competition analysis
* Opportunity filtering and ranking

The final goal is to recommend a small number of promising topics that can be reviewed by a human before content creation.

⸻

🧠 How It Works

The research pipeline follows this general structure:

Recent News / Trend Sources
          ↓
   Data Collection
          ↓
 Article Deduplication
          ↓
Candidate Keyword Generation
          ↓
 Search Demand Analysis
          ↓
Naver Blog Supply Analysis
          ↓
Competition / Opportunity Scoring
          ↓
 Quality & Threshold Filters
          ↓
 Recommended Keywords

The system intentionally separates data collection, analysis, and recommendation so that each stage can be tested and improved independently.

⸻

✨ Key Features

1. Trend & News Collection

Collects recent news and trend signals to identify topics receiving current attention.

The collector is designed to:

* Gather recent articles
* Normalize collected data
* Remove duplicate URLs and overlapping items
* Organize related events and topics
* Preserve source information for later analysis

⸻

2. Candidate Keyword Generation

Transforms collected topics into candidate search keywords.

Rather than evaluating only obvious headline terms, the system can generate multiple related keyword candidates from emerging events and topics.

⸻

3. Search Demand Analysis

Candidate keywords are evaluated using available search-demand signals.

The objective is not simply to find the largest keywords, but to find keywords where search interest may be attractive relative to existing content supply.

⸻

4. Blog Competition Analysis

The system measures the amount of competing Naver Blog content associated with candidate keywords.

Conceptually:

Keyword Opportunity
        =
Search Demand
        ÷
Competing Content Supply

The production scoring model can use additional filters and normalization rather than relying on this simple ratio alone.

⸻

5. Opportunity Detection

Keywords must pass predefined quality and opportunity thresholds before being recommended.

A key design principle is:

Do not lower thresholds simply because no keyword passes.

If no candidate satisfies the criteria, the system can return zero recommendations instead of forcing low-quality results.

⸻

6. Human-in-the-Loop Recommendation

The system is designed as a research assistant rather than a fully autonomous publishing bot.

AI Research
     ↓
2–3 Candidate Topics
     ↓
Human Review
     ↓
Content Creation
     ↓
Publishing

This allows automation to handle repetitive discovery work while keeping final editorial decisions under human control.

⸻

📊 Experimental Run

During one development run, the pipeline processed:

Metric	Result
API requests	97
News-related requests	93
Search-trend requests	1
Blog-related requests	3
Unique article URLs collected	2,469

In this run, no candidate passed the final opportunity threshold.

This result was intentionally preserved rather than weakening the selection criteria to manufacture recommendations.

Earlier mock testing was also used to evaluate the filtering pipeline before live API integration.

⸻

🏗️ Planned Repository Structure

naver-keyword-research-agent/
│
├── README.md
├── .gitignore
├── .env.example
├── requirements.txt
│
├── src/
│   ├── collector/
│   ├── keyword_analyzer/
│   ├── opportunity_detector/
│   └── recommender/
│
├── config/
│   └── config.example.yaml
│
├── docs/
│   ├── architecture.md
│   └── methodology.md
│
└── examples/
    └── sample_results.md

⸻

🛠️ Development Approach

This project is being developed using an AI-assisted engineering workflow.

AI coding and research tools are used for:

* System architecture
* Code generation and refactoring
* Data-pipeline design
* API integration
* Debugging
* Test planning
* Research methodology
* Result analysis

Generated implementations and recommendations are iteratively reviewed and tested rather than accepted blindly.

⸻

🔐 Security

API credentials are not stored in this repository.

Sensitive credentials such as:

API_KEY
SECRET_KEY
CUSTOMER_ID

should be stored locally using environment variables.

Example:

.env

A safe template can be provided through:

.env.example

without exposing real credentials.

⸻

🚧 Current Status

The project is under active development.

Current work focuses on:

* Improving keyword opportunity scoring
* Connecting live search-demand data
* Improving trend detection
* Reducing irrelevant candidate keywords
* Improving recommendation quality
* Evaluating results across multiple collection windows

⸻

🔮 Future Improvements

Planned extensions include:

* Scheduled trend collection
* Historical keyword tracking
* Search-demand / content-supply time series
* Automated opportunity reports
* Performance tracking after blog publication
* Feedback loops based on actual content performance

⸻

⚠️ Disclaimer

This project is an experimental research and automation system.

Keyword recommendations do not guarantee search traffic, ranking, or monetization performance. External API behavior and platform search algorithms may change over time.
