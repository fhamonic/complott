import click

@click.command()
@click.option('--name', '-n', default='World', help='Name to greet')
def command(name):
    """A sample subcommand"""
    click.echo(f"Hello, {name}!")