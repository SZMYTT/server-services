<?php
// progression.php
// Phase 4: Intelligence - Progression Algorithm
// Scans workout_logs. If a user hits 0 RIR for 2 consecutive sessions for the same exercise,
// flags for a weight increase.

$dbHost = getenv('DB_HOST') ?: 'localhost';
$dbPort = getenv('DB_PORT') ?: '5432';
$dbName = getenv('POSTGRES_DB') ?: 'systemos';
$dbUser = getenv('POSTGRES_USER') ?: 'postgres';
$dbPass = getenv('POSTGRES_PASSWORD') ?: 'postgres';

$dsn = "pgsql:host=$dbHost;port=$dbPort;dbname=$dbName";
try {
    $pdo = new PDO($dsn, $dbUser, $dbPass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
} catch (PDOException $e) {
    die("Database connection failed: " . $e->getMessage() . "\n");
}

echo "[PROGRESSION] Scanning for 0 RIR consecutive sets...\n";

// Get exercises that have 0 RIR in recent sessions
$stmt = $pdo->query("
    WITH ranked_sets AS (
        SELECT ws.exercise_id, ws.log_id, wl.started_at, ws.rir, ws.weight,
               ROW_NUMBER() OVER(PARTITION BY ws.exercise_id ORDER BY wl.started_at DESC) as rn
        FROM health.workout_sets ws
        JOIN health.workout_logs wl ON ws.log_id = wl.id
        WHERE ws.rir IS NOT NULL
    )
    SELECT exercise_id, 
           MAX(CASE WHEN rn = 1 THEN rir END) as last_rir,
           MAX(CASE WHEN rn = 2 THEN rir END) as prev_rir,
           MAX(CASE WHEN rn = 1 THEN weight END) as last_weight,
           MAX(CASE WHEN rn = 1 THEN log_id END) as last_log_id
    FROM ranked_sets
    WHERE rn <= 2
    GROUP BY exercise_id
    HAVING MAX(CASE WHEN rn = 1 THEN rir END) = 0 
       AND MAX(CASE WHEN rn = 2 THEN rir END) = 0
");

$flags = $stmt->fetchAll(PDO::FETCH_ASSOC);

if (!$flags) {
    echo "[PROGRESSION] No exercises require a weight increase.\n";
    exit(0);
}

foreach ($flags as $flag) {
    $exerciseId = $flag['exercise_id'];
    $currentWeight = (float)$flag['last_weight'];
    $suggestedWeight = $currentWeight + 2.5; // Suggest adding 2.5kg
    
    echo "[PROGRESSION] Exercise ID {$exerciseId} hit 0 RIR twice in a row. Suggesting weight increase from {$currentWeight} to {$suggestedWeight}.\n";
    
    // We could store this suggestion in a new table, or update the template.
    // For now, we will add a note to the latest workout log or update the template exercise target_weight.
    
    // Find template exercises using this exercise and update target weight
    $updateStmt = $pdo->prepare("
        UPDATE health.template_exercises 
        SET target_weight = :new_weight 
        WHERE exercise_id = :ex_id AND (target_weight = :old_weight OR target_weight IS NULL)
    ");
    $updateStmt->execute([
        ':new_weight' => $suggestedWeight,
        ':ex_id' => $exerciseId,
        ':old_weight' => $currentWeight
    ]);
    
    echo "  -> Updated template_exercises targets for Exercise ID {$exerciseId}.\n";
}

echo "[PROGRESSION] Scan complete.\n";
