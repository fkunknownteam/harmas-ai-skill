<?php
/**
 * _sqlgw.php — token-gated MySQL gateway for cpanel-hosting-control skill.
 *
 * Deployed by scripts/cpanel.py (sql command) into an .htaccess-protected dir.
 * Reads DB credentials from a sibling creds file the agent writes via cPanel,
 * never from the URL. One POST per call; SELECT/SHOW return rows, others return
 * affected rows. The token is a 32-byte random hex stored in the creds file.
 *
 * DELETE THIS FILE when the skill session is done (the script does it on exit).
 */
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
header('Content-Type: application/json; charset=utf-8');

$CREDS = __DIR__ . '/.sqlcreds.json';
if (!is_file($CREDS)) { http_response_code(404); echo json_encode(['error' => 'no creds']); exit; }
$cfg = json_decode(file_get_contents($CREDS), true);
if (!is_array($cfg) || empty($cfg['token']) || empty($cfg['db'])) {
    http_response_code(500); echo json_encode(['error' => 'bad creds']); exit;
}

$tok = $_SERVER['HTTP_X_SQL_TOKEN'] ?? '';
if (!hash_equals($cfg['token'], (string)$tok)) {
    http_response_code(401); echo json_encode(['error' => 'denied']); exit;
}

$sql = trim((string)($_POST['sql'] ?? ''));
if ($sql === '') { echo json_encode(['error' => 'empty sql']); exit; }

$dsn = 'mysql:host=localhost;dbname=' . $cfg['db'] . ';charset=utf8mb4';
try {
    $pdo = new PDO($dsn, $cfg['user'], $cfg['pass'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (Throwable $e) {
    http_response_code(502); echo json_encode(['error' => 'connect: ' . $e->getMessage()]); exit;
}

try {
    $st = $pdo->query($sql);
    if ($st === false) { echo json_encode(['error' => 'query failed']); exit; }
    if ($st->columnCount() > 0) {
        $rows = $st->fetchAll();
        echo json_encode(['rows' => $rows, 'count' => count($rows)], JSON_UNESCAPED_UNICODE);
    } else {
        echo json_encode(['affected' => $st->rowCount()]);
    }
} catch (Throwable $e) {
    http_response_code(400); echo json_encode(['error' => $e->getMessage()]);
}
