package com.example.voicelab.viewmodel

import android.app.Application
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.voicelab.data.AudioRepository
import com.example.voicelab.data.SettingsManager
import com.example.voicelab.player.AudioPlayer
import androidx.media3.common.Player
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.io.File

/**
 * UI State for the Home Screen
 */
data class VoiceUiState(
    val text: String = "",
    val selectedVoice: String = "en-US-GuyNeural",
    val isVoiceCloneMode: Boolean = false,
    val speakerId: String = "default",
    val isLoading: Boolean = false,
    val isPlaying: Boolean = false,
    val isEnded: Boolean = false,
    val downloadStatus: String? = null,
    val error: String? = null,
    val generatedFile: File? = null
)

class VoiceViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = AudioRepository(application)
    private val settingsManager = SettingsManager(application)
    val audioPlayer = AudioPlayer(application)

    init {
        audioPlayer.getPlayer().addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                _uiState.value = _uiState.value.copy(isPlaying = isPlaying)
            }

            override fun onPlaybackStateChanged(playbackState: Int) {
                _uiState.value = _uiState.value.copy(
                    isEnded = playbackState == Player.STATE_ENDED
                )
            }
        })
    }

    private val _uiState = mutableStateOf(VoiceUiState())
    val uiState: State<VoiceUiState> = _uiState

    val backendUrl = settingsManager.backendUrl

    fun onTextChange(newText: String) {
        _uiState.value = _uiState.value.copy(text = newText)
    }

    fun onVoiceChange(voice: String) {
        _uiState.value = _uiState.value.copy(selectedVoice = voice)
    }

    fun onSpeakerIdChange(id: String) {
        _uiState.value = _uiState.value.copy(speakerId = id)
    }

    fun toggleMode(isCloneMode: Boolean) {
        _uiState.value = _uiState.value.copy(isVoiceCloneMode = isCloneMode)
    }

    fun saveBackendUrl(url: String) {
        viewModelScope.launch {
            settingsManager.saveBackendUrl(url)
        }
    }

    /**
     * Trigger audio generation based on current mode.
     */
    fun generateAudio() {
        val currentState = _uiState.value
        if (currentState.text.isBlank()) {
            _uiState.value = currentState.copy(error = "Please enter some text")
            return
        }

        viewModelScope.launch {
            _uiState.value = currentState.copy(isLoading = true, error = null)
            
            val url = backendUrl.first()
            val result = if (currentState.isVoiceCloneMode) {
                repository.generateCloneTts(url, currentState.text, currentState.speakerId)
            } else {
                repository.generateTts(url, currentState.text, currentState.selectedVoice)
            }

            result.onSuccess { file ->
                _uiState.value = _uiState.value.copy(isLoading = false, generatedFile = file)
                audioPlayer.playFile(file)
            }.onFailure { exception ->
                _uiState.value = _uiState.value.copy(
                    isLoading = false, 
                    error = "Failed to connect to backend: ${exception.message}"
                )
            }
        }
    }

    /**
     * Downloads the generated audio file to the public Downloads folder.
     */
    fun downloadAudio() {
        val file = _uiState.value.generatedFile ?: return
        val result = repository.downloadAudio(file)
        result.onSuccess { message ->
            _uiState.value = _uiState.value.copy(downloadStatus = message)
        }.onFailure { exception ->
            _uiState.value = _uiState.value.copy(error = "Download failed: ${exception.message}")
        }
    }

    fun clearDownloadStatus() {
        _uiState.value = _uiState.value.copy(downloadStatus = null)
    }

    override fun onCleared() {
        super.onCleared()
        audioPlayer.release()
    }
}
