import subprocess

def test_build():
    result = subprocess.run(
        ['python', '-m', 'your_tool.cli', 'build', '--name', 'Test'],
        capture_output=True,
        text=True
    )
    assert "Hello, Test!" in result.stdout
