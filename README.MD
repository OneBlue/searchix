# Searchix

Searchix is a search and index tool for emails, powered by [postgresql's full text search](https://www.postgresql.org/docs/current/textsearch.html).

Searchix can theoretically work with sqlite, but performance will be poor.


## Usage


### Installation


```
$ git clone https://github.com/OneBlue/searchix.git
$ cd searchix
$ pip3 install -r requirements.txt

# Write database connection details in settings.py, and then initialize the database with:
$ ./manage.py makemigrations searchix
$ ./manage.py migrate

```

## Indexing


Searchix can either index emails in a folder as .eml files, or directly from stdin.

To index an entire folder, run:

```
./manage.py index </path/to/folder>
```

To index a singular email from stdin, run (note that --path is used to help deduplicate email entries in the database):

```
./manage.py index --stdin [--path <path>] < /path/to/.eml
```


## Search

Once the content is indexed, start the web server via:

```
./manage.py runserver
```

Then navigate to `http://127.0.0.1:8000`, click on `emails` and start searching using the search box at the top of the page
