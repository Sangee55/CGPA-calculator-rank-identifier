const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const app = express();

// Create or open the SQLite database
const db = new sqlite3.Database('mydatabase.db', (err) => {
  if (err) {
    console.error('Error opening database:', err);
  } else {
    console.log('Connected to the SQLite database.');
  }
});

// Serve static files (like CSS) from the "public" folder
app.use(express.static(path.join(__dirname, 'public')));

// Route to fetch users from the database and render them in HTML
app.get('/', (req, res) => {
  db.all('SELECT * FROM users', [], (err, rows) => {
    if (err) {
      res.status(500).send('Error fetching data');
    } else {
      res.send(`
        <html>
          <head>
            <title>Users List</title>
            <style>
              body { font-family: Arial, sans-serif; background-color: #f4f4f4; }
              table { width: 50%; margin: 50px auto; border-collapse: collapse; }
              th, td { padding: 10px; text-align: left; border: 1px solid #ddd; }
              th { background-color: #4CAF50; color: white; }
            </style>
          </head>
          <body>
            <h1 style="text-align: center;">Users List</h1>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>
                ${rows.map(user => `
                  <tr>
                    <td>${user.id}</td>
                    <td>${user.name}</td>
                    <td>${user.age}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </body>
        </html>
      `);
    }
  });
});

// Start the server on port 3000
app.listen(3000, () => {
  console.log('Server is running on http://localhost:3000');
});
