package com.example.voicelab.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.example.voicelab.viewmodel.VoiceUiState
import com.example.voicelab.viewmodel.VoiceViewModel
import java.io.File

@Composable
fun HomeScreen(
    viewModel: VoiceViewModel,
    onNavigateToSettings: () -> Unit
) {
    val uiState by viewModel.uiState
    
    HomeScreenContent(
        uiState = uiState,
        onTextChange = { viewModel.onTextChange(it) },
        onToggleMode = { viewModel.toggleMode(it) },
        onVoiceChange = { viewModel.onVoiceChange(it) },
        onSpeakerIdChange = { viewModel.onSpeakerIdChange(it) },
        onGenerate = { viewModel.generateAudio() },
        onPlay = { viewModel.audioPlayer.play() },
        onPause = { viewModel.audioPlayer.pause() },
        onStop = { viewModel.audioPlayer.stop() },
        onDownload = { viewModel.downloadAudio() },
        onNavigateToSettings = onNavigateToSettings
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreenContent(
    uiState: VoiceUiState,
    onTextChange: (String) -> Unit,
    onToggleMode: (Boolean) -> Unit,
    onVoiceChange: (String) -> Unit,
    onSpeakerIdChange: (String) -> Unit,
    onGenerate: () -> Unit,
    onPlay: () -> Unit,
    onPause: () -> Unit,
    onStop: () -> Unit,
    onDownload: () -> Unit,
    onNavigateToSettings: () -> Unit
) {
    val voices = listOf("en-US-GuyNeural", "en-US-JennyNeural", "en-GB-SoniaNeural")
    var expanded by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("TTS") },
                actions = {
                    IconButton(onClick = onNavigateToSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Text Input
            OutlinedTextField(
                value = uiState.text,
                onValueChange = onTextChange,
                label = { Text("Enter text to speak") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
                maxLines = 10,
                singleLine = false
            )

            // Mode Selector
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Mode: ")
                Spacer(modifier = Modifier.width(8.dp))
                FilterChip(
                    selected = !uiState.isVoiceCloneMode,
                    onClick = { onToggleMode(false) },
                    label = { Text("Standard TTS") }
                )
                Spacer(modifier = Modifier.width(8.dp))
                FilterChip(
                    selected = uiState.isVoiceCloneMode,
                    onClick = { onToggleMode(true) },
                    label = { Text("Voice Clone") }
                )
            }

            if (!uiState.isVoiceCloneMode) {
                // Voice Selector Dropdown
                ExposedDropdownMenuBox(
                    expanded = expanded,
                    onExpandedChange = { expanded = !expanded },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    OutlinedTextField(
                        value = uiState.selectedVoice,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Select Voice") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                        modifier = Modifier.menuAnchor().fillMaxWidth()
                    )
                    ExposedDropdownMenu(
                        expanded = expanded,
                        onDismissRequest = { expanded = false }
                    ) {
                        voices.forEach { voice ->
                            DropdownMenuItem(
                                text = { Text(voice) },
                                onClick = {
                                    onVoiceChange(voice)
                                    expanded = false
                                }
                            )
                        }
                    }
                }
            } else {
                // Speaker ID Input for Voice Clone
                OutlinedTextField(
                    value = uiState.speakerId,
                    onValueChange = onSpeakerIdChange,
                    label = { Text("Speaker ID") },
                    modifier = Modifier.fillMaxWidth()
                )
            }

            // Generate Button
            Button(
                onClick = onGenerate,
                modifier = Modifier.fillMaxWidth(),
                enabled = !uiState.isLoading
            ) {
                if (uiState.isLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        color = Color.White,
                        strokeWidth = 2.dp
                    )
                } else {
                    Text("Generate Audio")
                }
            }

            // Error Message
            uiState.error?.let {
                Text(text = it, color = MaterialTheme.colorScheme.error)
            }

            // Download Success Message
            uiState.downloadStatus?.let {
                Text(text = it, color = Color(0xFF4CAF50), modifier = Modifier.padding(top = 8.dp))
            }

            // Download Success Snackbar/Message
            uiState.downloadStatus?.let {
                Text(text = it, color = Color(0xFF4CAF50)) // Green color
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Audio Player Controls
            if (uiState.generatedFile != null) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = when {
                                uiState.isPlaying -> "Playing Audio..."
                                uiState.isEnded -> "Playback Finished"
                                else -> "Audio Ready"
                            },
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary
                        )
                        
                        Spacer(modifier = Modifier.height(8.dp))
                        
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceEvenly,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            // Play/Pause/Replay Toggle
                            FilledIconButton(
                                onClick = { if (uiState.isPlaying) onPause() else onPlay() },
                                modifier = Modifier.size(56.dp)
                            ) {
                                when {
                                    uiState.isPlaying -> Text("||")
                                    uiState.isEnded -> Icon(Icons.Default.Refresh, contentDescription = "Replay")
                                    else -> Icon(Icons.Default.PlayArrow, contentDescription = "Play")
                                }
                            }

                            // Download Button
                            OutlinedIconButton(
                                onClick = onDownload,
                                modifier = Modifier.size(48.dp)
                            ) {
                                Icon(Icons.Default.Download, contentDescription = "Download")
                            }

                            // Stop Button
                            IconButton(
                                onClick = onStop,
                                modifier = Modifier.size(48.dp)
                            ) {
                                // Simple Stop Icon representation
                                Box(modifier = Modifier.size(24.dp).graphicsLayer(alpha = 0.8f)) {
                                    Surface(
                                        modifier = Modifier.fillMaxSize(),
                                        color = MaterialTheme.colorScheme.error,
                                        shape = androidx.compose.foundation.shape.RoundedCornerShape(4.dp)
                                    ) {}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun HomeScreenPreview() {
    MaterialTheme {
        HomeScreenContent(
            uiState = VoiceUiState(text = "Hello, welcome to VoiceLab!"),
            onTextChange = {},
            onToggleMode = {},
            onVoiceChange = {},
            onSpeakerIdChange = {},
            onGenerate = {},
            onPlay = {},
            onPause = {},
            onStop = {},
            onDownload = {},
            onNavigateToSettings = {}
        )
    }
}
