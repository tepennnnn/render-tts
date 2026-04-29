package com.example.voicelab.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.voicelab.viewmodel.VoiceViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: VoiceViewModel,
    onNavigateBack: () -> Unit
) {
    val backendUrl by viewModel.backendUrl.collectAsState(initial = "")
    var urlInput by remember { mutableStateOf("") }

    // Update local state when flow emits
    LaunchedEffect(backendUrl) {
        urlInput = backendUrl
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back")
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
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Text(
                text = "API Configuration",
                style = MaterialTheme.typography.titleMedium
            )

            OutlinedTextField(
                value = urlInput,
                onValueChange = { urlInput = it },
                label = { Text("Backend Base URL") },
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("http://192.168.1.10:8000") }
            )

            Button(
                onClick = {
                    viewModel.saveBackendUrl(urlInput)
                    onNavigateBack()
                },
                modifier = Modifier.align(Alignment.End)
            ) {
                Text("Save and Back")
            }

            Spacer(modifier = Modifier.height(32.dp))
            
            Text(
                text = "Instructions:",
                style = MaterialTheme.typography.labelLarge
            )
            Text(
                text = "Enter the root URL of your TTS backend server. Make sure your Android device can reach this IP address on your local network.",
                style = MaterialTheme.typography.bodySmall
            )
        }
    }
}
